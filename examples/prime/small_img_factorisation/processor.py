import json
import logging
import os
import tempfile
import time
from typing import Optional, List
from pydantic import BaseModel

from simaas.dor.schemas import DataObject
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import Task, Job, JobStatus, Severity, Processor
from simaas.core.processor import ProcessorBase, ProgressListener, Namespace, ProcessorRuntimeError


class Parameters(BaseModel):
    N: int
    num_sub_jobs: int


class Result(BaseModel):
    factors: List[int]


class ProcessorFactorisation(ProcessorBase):
    """
    ProcessorFactorisation is a processor that performs a factorisation task by submitting sub-jobs to
    a factor search processor. The processor splits the factorisation process into smaller tasks
    and waits for them to complete. Once all sub-jobs are completed, it combines the results and writes
    the final output to a file.

    Attributes:
        _is_cancelled (bool): Flag indicating whether the processor has been cancelled.
        _use_threads (bool): Flag indicating whether threads are used for concurrent execution.
    """

    def __init__(self, proc_path: str, use_threads: bool = False) -> None:
        """
        Initializes the processor with the given path and optional threading option.

        Args:
            proc_path (str): Path to the processor's working directory.
            use_threads (bool, optional): Flag indicating whether threads should be used for concurrent execution.
                                          Defaults to False.
        """
        super().__init__(proc_path)
        self._is_cancelled = False
        self._use_threads = use_threads

    def run(
            self,
            wd_path: str,
            job: Job,
            listener: ProgressListener,
            namespace: Namespace,
            logger: logging.Logger
    ) -> None:
        """
        Executes the factorisation processor by performing the following steps:
        1. Reads parameters from the 'parameters' file in the working directory.
        2. Determines the number of steps for the factorisation based on the parameters.
        3. Searches for the factor search processor in the RTI (Runtime Infrastructure).
        4. Submits sub-jobs for factorisation using the parameters and processor ID.
        5. Waits for the sub-jobs to complete, collecting the factorisation results.
        6. Writes the final factorisation results to a file named 'result' in the working directory.
        7. Notifies the listener about the availability of the result and updates progress.

        Args:
            wd_path (str): Path to the working directory where input parameters and output files are stored.
            job (Job): The job object representing the task being processed.
            listener (ProgressListener): Listener for reporting progress updates, messages, and output availability.
            namespace (Namespace): Namespace for accessing RTI, keystore, and data objects.
            logger (logging.Logger): Logger for logging messages and errors during the processing.

        Notes:
            - The processor expects a `parameters` file in the `wd_path` containing the input parameters.
            - Sub-jobs are submitted to a factor search processor, which is identified by the image name 'proc-factor-search'.
            - The processor splits the factorisation task into smaller sub-jobs based on the number of sub-jobs specified in the parameters.
            - Results are collected and combined into a set of factors, which are sorted and written to the `result` file.
            - Progress and messages are reported using the provided listener.
        """

        # Step 1: Read parameters from the 'parameters' file
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)

        # Step 2: Calculate the number of steps for factorisation
        step = p.N // p.num_sub_jobs

        # Step 3: Find the factor search processor by image name
        proc_id: Optional[str] = None
        found: List[Processor] = namespace.rti.get_all_procs()
        for proc in found:
            if 'proc-small-img-factor-search' in proc.image_name:
                proc_id = proc.id
                break

        if proc_id is None:
            raise ProcessorRuntimeError("Factor search processor not found", details={
                'found': [item.model_dump() for item in found]
            })

        # Step 4: Create child jobs for factorisation
        pending = []
        for i in range(p.num_sub_jobs):
            start = 2 + i * step
            end = (i + 1) * step

            task = Task(
                proc_id=proc_id,
                user_iid=namespace.keystore().identity.id,
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
                    owner_iid=namespace.keystore().identity.id,
                    restricted_access=False,
                    content_encrypted=False,
                    target_node_iid=None
                )],
                budget=ResourceDescriptor(vcpus=1, memory=2048),
                namespace=namespace.name(),
                name=None,
                description=None,
            )
            result = namespace.rti.submit([task])
            job: Job = result[0]

            pending.append(job)

        # Step 5: Wait for sub-jobs to complete and collect results
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

                    # Collect factors from the successful sub-job result
                    with tempfile.TemporaryDirectory() as temp_dir:
                        result: DataObject = status.output['result']
                        content_path = os.path.join(temp_dir, f"{job.id}_result.json")
                        namespace.dor.get_content(result.obj_id, content_path)
                        with open(content_path, 'r') as f:
                            content: dict = json.load(f)
                            factors.update(content['factors'])

        # Step 6: If not cancelled, write the result to the 'result' file
        if not self._is_cancelled:
            result = Result(factors=sorted(list(factors)))
            result_path = os.path.join(wd_path, 'result')
            with open(result_path, 'w') as f:
                json.dump(result.model_dump(), f, indent=2)

            # Step 7: Inform the listener that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(Severity.INFO, f"we are done: {json.dumps(result.model_dump(), indent=2)}")

    def interrupt(self) -> None:
        """
        Cancels the ongoing factorisation process. This sets the `_is_cancelled` flag,
        which causes the processor to stop its execution at the next cancellation point.

        This method can be called to stop the processor mid-execution if needed, particularly
        if the job is taking too long or if the user requests cancellation.
        """
        self._is_cancelled = True
