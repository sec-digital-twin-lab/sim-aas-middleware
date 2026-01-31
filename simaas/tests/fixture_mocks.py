"""Mock classes and fixtures for testing."""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from examples.cosim.room.processor import RoomProcessor
from examples.cosim.thermostat.processor import ThermostatProcessor
from examples.prime.factor_search.processor import ProcessorFactorSearch
from examples.prime.factorisation.processor import ProcessorFactorisation
from simaas.core.helpers import get_timestamp_now, hash_json_object, generate_random_string
from simaas.core.keystore import Keystore
from simaas.core.processor import ProcessorBase, ProgressListener
from simaas.dor.api import DORInterface
from simaas.dor.schemas import DataObject, DataObjectProvenance, DataObjectRecipe, DORStatistics, TagValueType
from simaas.namespace.api import Namespace
from simaas.nodedb.schemas import NodeInfo
from simaas.rti.api import RTIInterface
from simaas.rti.schemas import Processor, JobStatus, Task, Job, Severity, BatchStatus

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class DummyProgressListener(ProgressListener):
    """Progress listener for testing processor execution."""

    def __init__(
            self, job_path: str, status: JobStatus, dor: DORInterface, expected_messages: Optional[List[str]] = None
    ):
        self._job_path = job_path
        self._status = status
        self._dor = dor
        self._expected_messages = expected_messages

    def on_progress_update(self, progress: float) -> None:
        """Handle progress update callback."""
        print(f"on_progress_update: {progress}")
        self._status.progress = progress

    def on_output_available(self, output_name: str) -> None:
        """Handle output available callback."""
        print(f"on_output_available: {output_name}")
        content_path = os.path.join(self._job_path, output_name)
        meta: DataObject = self._dor.add(content_path, 'JSON', 'json', 'someone')
        self._status.output[output_name] = meta

    def on_message(self, severity: Severity, message: str) -> None:
        """Handle message callback."""
        print(f"on_message: {severity} {message}")
        if self._expected_messages is not None:
            assert message == self._expected_messages[0]
            self._expected_messages.pop(0)


class DummyNamespace(Namespace):
    """In-memory namespace implementation for testing."""

    class DummyDOR(DORInterface):
        """In-memory DOR implementation for testing."""

        def __init__(self):
            self._next_obj_id: int = 0
            self._meta: Dict[str, DataObject] = {}
            self._content: Dict[str, dict] = {}

        def type(self) -> str:
            return 'dummy'

        def search(self, patterns: Optional[List[str]] = None, owner_iid: Optional[str] = None,
                   data_type: Optional[str] = None, data_format: Optional[str] = None,
                   c_hashes: Optional[List[str]] = None) -> List[DataObject]:
            pass

        def statistics(self) -> DORStatistics:
            pass

        def add(self, content_path: str, data_type: str, data_format: str, owner_iid: str,
                creators_iid: Optional[List[str]] = None, access_restricted: Optional[bool] = False,
                content_encrypted: Optional[bool] = False, license: Optional[DataObject.License] = None,
                tags: Optional[Dict[str, TagValueType]] = None,
                recipe: Optional[DataObjectRecipe] = None) -> DataObject:

            obj_id = str(self._next_obj_id)
            self._next_obj_id += 1

            with open(content_path, 'r') as f:
                self._content[obj_id] = json.load(f)
                c_hash: str = hash_json_object(self._content[obj_id]).hex()

            meta = DataObject(
                obj_id=obj_id,
                c_hash=c_hash,
                data_type=data_type,
                data_format=data_format,
                created=DataObject.CreationDetails(
                    timestamp=get_timestamp_now(),
                    creators_iid=[]
                ),
                owner_iid=owner_iid,
                access_restricted=access_restricted,
                access=[],
                tags=tags if tags else {},
                last_accessed=get_timestamp_now(),
                custodian=None,
                content_encrypted=content_encrypted,
                license=license,
                recipe=recipe
            )
            self._meta[obj_id] = meta
            return meta

        def remove(self, obj_id: str) -> Optional[DataObject]:
            pass

        def get_meta(self, obj_id: str) -> Optional[DataObject]:
            return self._meta[obj_id]

        def get_content(self, obj_id: str, content_path: str) -> None:
            with open(content_path, 'w') as f:
                json.dump(self._content[obj_id], f, indent=2)

        def get_provenance(self, c_hash: str) -> Optional[DataObjectProvenance]:
            pass

        def grant_access(self, obj_id: str, user_iid: str) -> DataObject:
            pass

        def revoke_access(self, obj_id: str, user_iid: str) -> DataObject:
            pass

        def transfer_ownership(self, obj_id: str, new_owner_iid: str) -> DataObject:
            pass

        def update_tags(self, obj_id: str, tags: List[DataObject.Tag]) -> DataObject:
            pass

        def remove_tags(self, obj_id: str, keys: List[str]) -> DataObject:
            pass

    class DummyRTI(RTIInterface):
        """In-memory RTI implementation for testing."""

        def __init__(self, namespace):
            self._namespace = namespace
            self._procs: Dict[str, Processor] = {
                'factor_search': Processor(
                    id="factor_search",
                    state=Processor.State.READY,
                    image_name='proc-factor-search',
                    ports=None,
                    volumes=[],
                    gpp=None,
                    error=None
                ),
                'factorisation': Processor(
                    id="factorisation",
                    state=Processor.State.READY,
                    image_name='proc-factorisation',
                    ports=None,
                    volumes=[],
                    gpp=None,
                    error=None
                ),
                'room': Processor(
                    id="room",
                    state=Processor.State.READY,
                    image_name='proc-room',
                    ports=None,
                    volumes=[],
                    gpp=None,
                    error=None
                ),
                'thermostat': Processor(
                    id="thermostat",
                    state=Processor.State.READY,
                    image_name='proc-thermostat',
                    ports=None,
                    volumes=[],
                    gpp=None,
                    error=None
                )
            }
            self._procs_classes: Dict[str, type] = {
                'factor_search': ProcessorFactorSearch,
                'factorisation': ProcessorFactorisation,
                'room': RoomProcessor,
                'thermostat': ThermostatProcessor
            }
            self._instances: Dict[str, ProcessorBase] = {}
            self._jobs: Dict[str, Job] = {}
            self._status: Dict[str, JobStatus] = {}
            self._batch: Dict[str, BatchStatus] = {}
            self._next_job_id: int = 0
            self._keystore = Keystore.new('dummy')

        def type(self) -> str:
            return 'dummy'

        def get_all_procs(self) -> List[Processor]:
            return list(self._procs.values())

        def get_proc(self, proc_id: str) -> Optional[Processor]:
            return self._procs.get(proc_id, None)

        def submit(self, tasks: List[Task]) -> List[Job]:
            def execute() -> None:
                try:
                    with tempfile.TemporaryDirectory() as wd_path:
                        status.state = JobStatus.State.RUNNING
                        in0: Task.InputValue = task.input[0]
                        with open(os.path.join(wd_path, in0.name), 'w') as f:
                            json.dump(in0.value, f, indent=2)

                        print(f"job:{job_id} -> {in0.value}")
                        self._instances[job_id].run(
                            wd_path, job, DummyProgressListener(wd_path, status, self._namespace.dor), self._namespace, None
                        )
                        if status.state == JobStatus.State.RUNNING:
                            status.state = JobStatus.State.SUCCESSFUL
                        else:
                            print(f"job:{job_id} was cancelled")

                except Exception as e:
                    status.state = JobStatus.State.FAILED
                    print(e)

            result: List[Job] = []
            batch_id: Optional[str] = generate_random_string(8) if len(tasks) > 1 else None
            for task in tasks:
                job_id = str(self._next_job_id)
                self._next_job_id += 1
                job = Job(
                    id=job_id,
                    batch_id=batch_id,
                    task=task,
                    retain=True,
                    custodian=NodeInfo(
                        identity=self._keystore.identity,
                        last_seen=get_timestamp_now(),
                        dor_service='dummy',
                        rti_service='dummy',
                        p2p_address='in-memory',
                        rest_address=None,
                        retain_job_history=True,
                        strict_deployment=False
                    ),
                    proc_name=task.proc_id,
                    t_submitted=get_timestamp_now()
                )
                self._jobs[job_id] = job

                status = JobStatus(
                    state=JobStatus.State.INITIALISED,
                    progress=0,
                    output={},
                    notes={},
                    errors=[],
                    message=None
                )
                self._status[job_id] = status

                # create instance
                proc_type = self._procs_classes[task.proc_id]
                self._instances[job_id] = proc_type(os.path.join(BASE_DIR, 'examples', 'prime', task.proc_id))

                thread = threading.Thread(target=execute, args=())
                thread.start()

                result.append(job)

            return result

        def get_job_status(self, job_id: str) -> JobStatus:
            return self._status[job_id]

        def put_batch_status(self, batch_status: BatchStatus):
            self._batch[batch_status.batch_id] = batch_status

        def get_batch_status(self, batch_id: str) -> BatchStatus:
            return self._batch.get(batch_id)

        def job_cancel(self, job_id: str) -> JobStatus:
            self._instances[job_id].interrupt()
            self._status[job_id].state = JobStatus.State.CANCELLED

        def job_purge(self, job_id: str) -> JobStatus:
            pass

    def __init__(self):
        super().__init__(DummyNamespace.DummyDOR(), DummyNamespace.DummyRTI(self))
        self._keystore = Keystore.new('dummy')

    def id(self) -> str:
        return 'dummy'

    def custodian_address(self) -> str:
        pass

    def name(self) -> str:
        return 'dummy'

    def keystore(self) -> Keystore:
        return self._keystore

    def destroy(self) -> None:
        pass


@pytest.fixture(scope='session')
def dummy_namespace():
    """Session-scoped fixture providing a DummyNamespace instance."""
    namespace = DummyNamespace()
    yield namespace
