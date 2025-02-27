import json
import logging
import os
import tempfile
import time
from typing import Optional, List
from pydantic import BaseModel

from simaas.dor.schemas import DataObject
from simaas.rti.schemas import Task, Job, JobStatus, Severity, Processor
from simaas.core.processor import ProcessorBase, ProgressListener, Namespace, ProcessorRuntimeError


class Parameters(BaseModel):
    N: int
    num_sub_jobs: int


class Result(BaseModel):
    factors: List[int]


class ProcessorFactorisation(ProcessorBase):
    def __init__(self, proc_path: str, use_threads: bool = False) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False
        self._use_threads = use_threads

    def run(self, wd_path: str, listener: ProgressListener, namespace: Namespace, logger: logging.Logger) -> None:
        # read the parameters
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)

        # determine number of steps
        step = p.N // p.num_sub_jobs

        # find the processor
        proc_id: Optional[str] = None
        found: List[Processor] = namespace.rti.get_all_procs()
        for proc in found:
            if 'proc-factor-search' in proc.image_name:
                proc_id = proc.id
                break

        if proc_id is None:
            raise ProcessorRuntimeError("Factor search processor not found", details={
                'found': [item.model_dump() for item in found]
            })

        # create child jobs
        pending = []
        for i in range(p.num_sub_jobs):
            start = 2 + i * step
            end = (i + 1) * step

            job: Job = namespace.rti.submit('factor_search', Task(
                proc_id='factor_search',
                user_iid='someone',
                input=[Task.InputValue(
                    name='parameters',
                    type='value',
                    value={
                        'start': start,
                        'end': end,
                        'number': p.N
                    }
                )],
                output=[Task.Output(
                    name='result',
                    owner_iid='someone',
                    restricted_access=False,
                    content_encrypted=False,
                    target_node_iid=None
            )],
                name=None,
                description=None,
                budget=None
            ))

            pending.append(job)

        # wait for sub-jobs to complete
        factors = set()
        while len(pending) > 0:
            time.sleep(0.2)

            if self._is_cancelled:
                job = pending.pop(0)
                namespace.rti.job_cancel(job.id)

            else:
                job = pending[0]
                status: JobStatus = namespace.rti.get_job_status(job.id)
                if status.state == JobStatus.State.SUCCESSFUL:
                    pending.pop(0)

                    # collect results
                    with tempfile.TemporaryDirectory() as temp_dir:
                        result: DataObject = status.output['result']

                        content_path=os.path.join(temp_dir, f"{job.id}_result.json")
                        namespace.dor.get_content(result.obj_id, content_path)
                        with open(content_path, 'r') as f:
                            content: dict = json.load(f)
                            factors.update(content['factors'])

        if not self._is_cancelled:
            # create the result and write to file
            result = Result(factors=sorted(list(factors)))
            result_path = os.path.join(wd_path, 'result')
            with open(result_path, 'w') as f:
                json.dump(result.model_dump(), f, indent=2)

            # inform the RTI that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(Severity.INFO, f"we are done: {json.dumps(result.model_dump(), indent=2)}")

    def interrupt(self) -> None:
        self._is_cancelled = True
