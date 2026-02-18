"""Test data factories for creating test objects."""

import asyncio
import json
import logging
import os
import shutil
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from examples.simple.abc.processor import write_value
from simaas.cli.cmd_job_runner import JobRunner
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.core.processor import ProgressListener, ProcessorBase, ProcessorRuntimeError, Namespace
from simaas.dor.api import DORProxy
from simaas.dor.schemas import DataObject, ProcessorDescriptor, GitProcessorPointer
from simaas.helpers import PortMaster
from simaas.node.base import Node
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.p2p.base import P2PAddress
from simaas.rti.base import DBJobInfo
from simaas.rti.protocol import P2PInterruptJob
from simaas.rti.schemas import Task, Job, JobStatus, Severity, ExitCode, JobResult

repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


def prepare_plain_job_folder(jobs_root_path: str, job_id: str, a: Any = 1, b: Any = 1) -> str:
    """Create a job folder with input files a and b."""
    job_path = os.path.join(jobs_root_path, job_id)
    os.makedirs(job_path, exist_ok=True)
    write_value(os.path.join(job_path, 'a'), a)
    write_value(os.path.join(job_path, 'b'), b)
    return job_path


@dataclass
class TaskBuilder:
    """Builder pattern for creating Task objects."""
    proc_id: str
    user_iid: str
    name: Optional[str] = None
    description: Optional[str] = None
    budget_vcpus: int = 1
    budget_memory: int = 1024
    namespace: Optional[str] = None
    inputs: List[Union[Task.InputValue, Task.InputReference]] = field(default_factory=list)
    outputs: List[Task.Output] = field(default_factory=list)

    def with_name(self, name: str) -> 'TaskBuilder':
        """Set the task name."""
        self.name = name
        return self

    def with_description(self, description: str) -> 'TaskBuilder':
        """Set the task description."""
        self.description = description
        return self

    def with_budget(self, vcpus: int = 1, memory: int = 1024) -> 'TaskBuilder':
        """Set the resource budget for the task."""
        self.budget_vcpus = vcpus
        self.budget_memory = memory
        return self

    def with_namespace(self, namespace: str) -> 'TaskBuilder':
        """Set the namespace for the task."""
        self.namespace = namespace
        return self

    def with_input_value(self, name: str, value: dict) -> 'TaskBuilder':
        """Add a by-value input to the task."""
        self.inputs.append(Task.InputValue(name=name, type='value', value=value))
        return self

    def with_input_reference(
        self,
        name: str,
        obj_id: str,
        user_signature: Optional[str] = None,
        c_hash: Optional[str] = None
    ) -> 'TaskBuilder':
        """Add a by-reference input to the task."""
        self.inputs.append(Task.InputReference(
            name=name,
            type='reference',
            obj_id=obj_id,
            user_signature=user_signature,
            c_hash=c_hash
        ))
        return self

    def with_output(
        self,
        name: str,
        owner_iid: str,
        restricted_access: bool = False,
        content_encrypted: bool = False,
        target_node_iid: Optional[str] = None
    ) -> 'TaskBuilder':
        """Add an output definition to the task."""
        self.outputs.append(Task.Output(
            name=name,
            owner_iid=owner_iid,
            restricted_access=restricted_access,
            content_encrypted=content_encrypted,
            target_node_iid=target_node_iid
        ))
        return self

    def build(self) -> Task:
        """Build and return the Task object."""
        return Task(
            proc_id=self.proc_id,
            user_iid=self.user_iid,
            input=self.inputs,
            output=self.outputs,
            name=self.name,
            description=self.description,
            budget=ResourceDescriptor(vcpus=self.budget_vcpus, memory=self.budget_memory),
            namespace=self.namespace
        )


def create_abc_task(
    proc_id: str,
    owner: Keystore,
    a: int = 1,
    b: int = 1,
    memory: int = 1024,
    namespace: Optional[str] = None
) -> Task:
    """Factory function for creating ABC processor tasks."""
    builder = (TaskBuilder(proc_id, owner.identity.id)
               .with_input_value('a', {'v': a})
               .with_input_value('b', {'v': b})
               .with_output('c', owner.identity.id)
               .with_budget(memory=memory))

    if namespace:
        builder.with_namespace(namespace)

    return builder.build()


def create_ping_task(
    proc_id: str,
    owner: Keystore,
    message: str = "ping",
    memory: int = 1024,
    namespace: Optional[str] = None
) -> Task:
    """Factory function for creating Ping processor tasks."""
    builder = (TaskBuilder(proc_id, owner.identity.id)
               .with_input_value('message', {'content': message})
               .with_output('response', owner.identity.id)
               .with_budget(memory=memory))

    if namespace:
        builder.with_namespace(namespace)

    return builder.build()


def prepare_data_object(content_path: str, node: Node, v: int = 1, data_type: str = 'JSONObject',
                        data_format: str = 'json', access: List[Identity] = None) -> DataObject:
    """Create a test data object with JSON content."""
    with open(content_path, 'w') as f:
        json.dump({'v': v}, f, indent=2)

    proxy = DORProxy(node.rest.address())
    if access:
        obj = proxy.add_data_object(content_path, node.identity, True, False, data_type, data_format)
        for identity in access:
            obj = proxy.grant_access(obj.obj_id, node.keystore, identity)
    else:
        obj = proxy.add_data_object(content_path, node.identity, False, False, data_type, data_format)

    return obj


def prepare_proc_path(proc_path: str) -> GitProcessorPointer:
    """Copy ABC processor to path and return GitProcessorPointer."""
    proc_abc_path = os.path.join(examples_path, 'simple', 'abc')
    shutil.copytree(proc_abc_path, proc_path)

    descriptor_path = os.path.join(proc_path, 'descriptor.json')
    with open(descriptor_path, 'r') as f:
        content = json.load(f)
        proc_descriptor = ProcessorDescriptor.model_validate(content)

    gpp_path = os.path.join(proc_path, 'gpp.json')
    with open(gpp_path, 'w') as f:
        gpp = GitProcessorPointer(
            repository='local', commit_id='commit_id', proc_path='processor', proc_descriptor=proc_descriptor
        )
        json.dump(gpp.model_dump(), f, indent=2)

        return gpp


def run_job_cmd(
        job_path: str, proc_path: str, service_address: str, custodian_address: str, custodian_pub_key: str, job_id: str
) -> None:
    """Run a job command in a separate thread."""
    try:
        cmd = JobRunner()
        args = {
            'job_path': job_path,
            'proc_path': proc_path,
            'service_address': service_address,
            'custodian_address': custodian_address,
            'custodian_pub_key': custodian_pub_key,
            'job_id': job_id
        }
        cmd.execute(args)
    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
        print(trace)


async def execute_job(
        wd_parent_path: str, custodian: Node, job_id: str,
        a: Union[dict, int, str, DataObject], b: Union[dict, int, str, DataObject],
        user: Identity = None, sig_a: str = None, sig_b: str = None, target_node: Node = None, cancel: bool = False,
        batch_id: Optional[str] = None
) -> JobStatus:
    """Execute a job and wait for completion."""
    from simaas.plugins.builtins.rti_docker import DockerRTIService

    rti: DockerRTIService = custodian.rti
    user = user if user else custodian.identity

    wd_path = os.path.join(wd_parent_path, job_id)
    os.makedirs(wd_path)

    proc_path = os.path.join(wd_path, 'processor')
    gpp: GitProcessorPointer = prepare_proc_path(proc_path)

    if a is None:
        a = {'v': 1}
    elif isinstance(a, (int, str)):
        a = {'v': a}

    if b is None:
        b = {'v': 1}
    elif isinstance(b, (int, str)):
        b = {'v': b}

    a = Task.InputReference(name='a', type='reference', obj_id=a.obj_id, user_signature=sig_a, c_hash=None) \
        if isinstance(a, DataObject) else Task.InputValue(name='a', type='value', value=a)

    b = Task.InputReference(name='b', type='reference', obj_id=b.obj_id, user_signature=sig_b, c_hash=None) \
        if isinstance(b, DataObject) else Task.InputValue(name='b', type='value', value=b)

    c = Task.Output(
        name='c',
        owner_iid=user.id,
        restricted_access=False, content_encrypted=False,
        target_node_iid=target_node.identity.id if target_node else custodian.identity.id
    )

    task = Task(
        proc_id='fake_proc_id', user_iid=user.id, input=[a, b], output=[c], name='test', description='',
        budget=None, namespace=None,
    )

    job = Job(
        id=job_id, batch_id=batch_id, task=task, retain=False, custodian=custodian.info,
        proc_name=gpp.proc_descriptor.name, t_submitted=0
    )
    status = JobStatus(
        state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={}, errors=[], message=None
    )

    service_address = PortMaster.generate_p2p_address()

    with rti._session_maker() as session:
        record = DBJobInfo(
            id=job.id, batch_id=batch_id, proc_id=task.proc_id, user_iid=user.id, status=status.model_dump(),
            job=job.model_dump(), runner={
                '__ports': {
                    '6000/tcp': service_address
                },
                'ports': {
                    '6000/tcp': service_address
                }
            }
        )
        session.add(record)
        session.commit()

    threading.Thread(
        target=run_job_cmd,
        args=(wd_path, proc_path, service_address, custodian.p2p.address(), custodian.identity.c_public_key, job_id)
    ).start()

    if cancel:
        runner_identity = None
        runner_address = None
        for i in range(10):
            await asyncio.sleep(1)

            with rti._session_maker() as session:
                record = session.get(DBJobInfo, job_id)
                if 'identity' in record.runner and 'address' in record.runner:
                    runner_identity: Identity = Identity.model_validate(record.runner['identity'])
                    runner_address: str = record.runner['address']
                    break

        if runner_identity is None or runner_address is None:
            assert False

        # Simulate RTI cancel flow: mark cancelled in DB first, then send interrupt
        rti.mark_job_cancelled(job_id)

        await P2PInterruptJob.perform(P2PAddress(
            address=runner_address,
            curve_secret_key=custodian.keystore.curve_secret_key(),
            curve_public_key=custodian.keystore.curve_public_key(),
            curve_server_key=runner_identity.c_public_key
        ))

    # Wait for job completion with timeout
    timeout = 60  # seconds
    elapsed = 0
    while elapsed < timeout:
        status: JobStatus = await rti.get_job_status(job.id)

        if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
            return status

        await asyncio.sleep(0.5)
        elapsed += 0.5

    # Timeout reached - return current status for debugging
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s. Last state: {status.state}")


class ProcessorRunner(threading.Thread, ProgressListener):
    """Thread-based processor runner with progress tracking."""

    def __init__(
            self, proc: ProcessorBase, wd_path: str, log_level: int = logging.INFO,
            job: Job = None, namespace: Namespace = None
    ) -> None:
        super().__init__()

        self._mutex = threading.Lock()
        self._proc = proc
        self._wd_path = wd_path
        self._job = job
        self._namespace = namespace
        self._interrupted = False

        log_path = os.path.join(wd_path, 'job.log')
        self._logger = get_logger('cli.job_runner', 'runner', level=log_level, custom_log_path=log_path)

        self._job_status = JobStatus(state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={},
                                     errors=[], message=None)
        self._store_job_status()

    def on_progress_update(self, progress: int) -> None:
        self._logger.info(f"on_progress_update: progress={progress}")
        self._job_status.progress = progress
        self._store_job_status()

    def on_output_available(self, output_name: str) -> None:
        if output_name not in self._job_status.output:
            self._logger.info(f"on_output_available: output_name={output_name}")
            self._job_status.output[output_name] = None
            self._store_job_status()

    def on_message(self, severity: Severity, message: str) -> None:
        self._logger.info(f"on_message: severity={severity} message={message}")
        self._job_status.message = JobStatus.Message(severity=severity, content=message)
        self._store_job_status()

    def _store_job_status(self) -> None:
        job_status_path = os.path.join(self._wd_path, 'job.status')
        with open(job_status_path, 'w') as f:
            json.dump(self._job_status.model_dump(), f, indent=2)

    def _write_exitcode(self, exitcode: ExitCode, e: Exception = None) -> None:
        exitcode_path = os.path.join(self._wd_path, 'job.exitcode')
        with open(exitcode_path, 'w') as f:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            result = JobResult(exitcode=exitcode, trace=trace)
            json.dump(result.model_dump(), f, indent=2)

    def run(self) -> None:
        try:
            self._logger.info(f"begin processing job at {self._wd_path}")

            self._proc.run(self._wd_path, self._job, self, self._namespace, self._logger)

            if self._interrupted:
                self._logger.info(f"end processing job at {self._wd_path} -> INTERRUPTED")
                self._write_exitcode(ExitCode.INTERRUPTED)
            else:
                self._logger.info(f"end processing job at {self._wd_path} -> DONE")
                self._write_exitcode(ExitCode.DONE)

        except ProcessorRuntimeError as e:
            self._logger.error(f"end processing job at {self._wd_path} -> FAILED: {e.reason}")
            self._write_exitcode(ExitCode.ERROR, e)

        except Exception as e:
            self._logger.error(f"end processing job at {self._wd_path} -> FAILED: {e}")
            self._write_exitcode(ExitCode.ERROR, e)

    def status(self) -> JobStatus:
        with self._mutex:
            return self._job_status

    def interrupt(self) -> JobStatus:
        with self._mutex:
            self._logger.info(f"attempt to interrupt job at {self._wd_path}...")
            self._interrupted = True
            self._proc.interrupt()
            return self._job_status
