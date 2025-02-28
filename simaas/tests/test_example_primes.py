import json
import logging
import os
import tempfile
import threading
import time
from typing import List, Optional, Dict, Union

import pytest
from simaas.core.logging import Logging
from simaas.core.keystore import Keystore
from simaas.nodedb.schemas import NodeInfo
from simaas.core.helpers import get_timestamp_now, hash_json_object
from simaas.core.processor import ProgressListener, ProcessorBase
from examples.prime.factor_search.processor import Parameters as FactorSearchParameters, ProcessorFactorSearch, \
    Result
from examples.prime.factorisation.processor import Parameters as FactorisationParameters, ProcessorFactorisation
from simaas.dor.api import DORInterface
from simaas.dor.schemas import DataObject, DataObjectProvenance, DataObjectRecipe, DORStatistics
from simaas.namespace.api import Namespace
from simaas.rti.api import RTIInterface
from simaas.rti.schemas import Severity, JobStatus, Task, Job, Processor
from simaas.tests.conftest import add_test_processor, BASE_DIR

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


class DummyProgressListener(ProgressListener):
    def __init__(self, job_path: str, status: JobStatus, dor: DORInterface):
        self._job_path = job_path
        self._status = status
        self._dor = dor

    def on_progress_update(self, progress: float) -> None:
        print(f"on_progress_update: {progress}")
        self._status.progress = progress

    def on_output_available(self, output_name: str) -> None:
        print(f"on_output_available: {output_name}")
        content_path = os.path.join(self._job_path, output_name)
        meta: DataObject = self._dor.add(content_path, 'JSON', 'json', 'someone')
        self._status.output[output_name] = meta

    def on_message(self, severity: Severity, message: str) -> None:
        print(f"on_message: {severity} {message}")


class DummyNamespace(Namespace):
    class DummyDOR(DORInterface):
        def __init__(self):
            self._next_obj_id: int = 0
            self._meta: Dict[str, DataObject] = {}
            self._content: Dict[str, dict] = {}


        def search(self, patterns: Optional[List[str]] = None, owner_iid: Optional[str] = None,
                   data_type: Optional[str] = None, data_format: Optional[str] = None,
                   c_hashes: Optional[List[str]] = None) -> List[DataObject]:
            pass

        def statistics(self) -> DORStatistics:
            pass

        def add(self, content_path: str, data_type: str, data_format: str, owner_iid: str,
                creators_iid: Optional[List[str]] = None, access_restricted: Optional[bool] = False,
                content_encrypted: Optional[bool] = False, license: Optional[DataObject.License] = None,
                tags: Optional[Dict[str, Union[str, int, float, bool, List, Dict]]] = None,
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
        def __init__(self, namespace):
            self._namespace = namespace
            self._procs: Dict[str, Processor] = {
                'factor_search': Processor(
                    id="factor_search",
                    state=Processor.State.READY,
                    image_name='proc-factor-search',
                    gpp=None,
                    error=None
                ),
                'factorisation': Processor(
                    id="factorisation",
                    state=Processor.State.READY,
                    image_name='proc-factorisation',
                    gpp=None,
                    error=None
                )
            }
            self._procs_classes: Dict[str, type] = {
                'factor_search': ProcessorFactorSearch,
                'factorisation': ProcessorFactorisation
            }
            self._instances: Dict[str, ProcessorBase] = {}
            self._jobs: Dict[str, Job] = {}
            self._status: Dict[str, JobStatus] = {}
            self._next_job_id: int = 0
            self._keystore = Keystore.new('dummy')

        def get_all_procs(self) -> List[Processor]:
            return list(self._procs.values())

        def get_proc(self, proc_id: str) -> Optional[Processor]:
            return self._procs.get(proc_id, None)

        def submit(self, proc_id: str, task: Task) -> Job:
            job_id = str(self._next_job_id)
            self._next_job_id += 1
            job = Job(
                id=job_id,
                task=task,
                retain=True,
                custodian=NodeInfo(
                    identity=self._keystore.identity,
                    last_seen=get_timestamp_now(),
                    dor_service=True,
                    rti_service=True,
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
            proc_type = self._procs_classes[proc_id]
            self._instances[job_id] = proc_type(os.path.join(BASE_DIR, 'examples', 'prime', proc_id))

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

            thread = threading.Thread(target=execute, args=())
            thread.start()

            return self._jobs[job_id]

        def get_job_status(self, job_id: str) -> JobStatus:
            return self._status[job_id]

        def job_cancel(self, job_id: str) -> JobStatus:
            self._instances[job_id].interrupt()
            self._status[job_id].state =  JobStatus.State.CANCELLED

        def job_purge(self, job_id: str) -> JobStatus:
            pass

    def __init__(self):
        super().__init__(DummyNamespace.DummyDOR(), DummyNamespace.DummyRTI(self))
        self._keystore = Keystore.new('dummy')

    def id(self) -> str:
        return 'dummy'

    def name(self) -> str:
        return 'dummy'

    def keystore(self) -> Keystore:
        return self._keystore

    def destroy(self) -> None:
        pass


@pytest.fixture(scope='session')
def dummy_namespace():
    namespace = DummyNamespace()
    yield namespace


def test_proc_factor_search(dummy_namespace):
    N = 100
    num_sub_jobs = 1
    step = N // num_sub_jobs
    i = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        # create parameters file
        start = 2 + i * step
        end = (i + 1) * step
        parameters = FactorSearchParameters(start=start, end=end, number=N)
        parameters_path = os.path.join(temp_dir, 'parameters')
        with open(parameters_path, 'w') as f:
            json.dump(parameters.model_dump(), f, indent=2)

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'prime', 'factor_search')
        proc = ProcessorFactorSearch(proc_path)
        proc.run(temp_dir, None, DummyProgressListener(temp_dir, status, dummy_namespace.dor), dummy_namespace, None)

        # read the result
        result_path = os.path.join(temp_dir, 'result')
        assert os.path.isfile(result_path)
        with open(result_path, 'r') as f:
            result: dict = json.load(f)
            result: Result = Result.model_validate(result)

        # print the result
        print(result.factors)
        assert(result.factors == [2, 4, 5, 10, 20, 25, 50])
        if len(result.factors) == 0:
            print(f"N={N} is prime")
        else:
            print(f"N={N} is NOT prime")


def test_proc_factorisation(dummy_namespace):
    N = 100
    num_sub_jobs = 2

    with tempfile.TemporaryDirectory() as temp_dir:
        # create parameters file
        parameters = FactorisationParameters(N=N, num_sub_jobs=num_sub_jobs)
        parameters_path = os.path.join(temp_dir, 'parameters')
        with open(parameters_path, 'w') as f:
            json.dump(parameters.model_dump(), f, indent=2)

        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )

        # create the processor and run it
        proc_path = os.path.join(BASE_DIR, 'examples', 'prime', 'factorisation')
        proc = ProcessorFactorisation(proc_path)
        proc.run(temp_dir, None, DummyProgressListener(temp_dir, status, dummy_namespace.dor), dummy_namespace, None)

        # read the result
        result_path = os.path.join(temp_dir, 'result')
        assert os.path.isfile(result_path)
        with open(result_path, 'r') as f:
            result: dict = json.load(f)
            result: Result = Result.model_validate(result)

        # print the result
        print(result.factors)
        assert(result.factors == [2, 4, 5, 10, 20, 25, 50])
        if len(result.factors) == 2 and 1 in result.factors and N in result.factors:
            print(f"N={N} is prime")
        else:
            print(f"N={N} is NOT prime")


def test_proc_factorisation_cancel(dummy_namespace):
    N = 987654321987
    num_sub_jobs = 2

    job: Job = dummy_namespace.rti.submit('factorisation', Task(
        proc_id='factorisation',
        user_iid='someone',
        input=[Task.InputValue(
            name='parameters',
            type='value',
            value={
                'N': N,
                'num_sub_jobs': num_sub_jobs
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

    # wait for a bit...
    time.sleep(5)

    # cancel it
    dummy_namespace.rti.job_cancel(job.id)

    status: JobStatus = dummy_namespace.rti.get_job_status(job.id)
    assert status.state == JobStatus.State.CANCELLED


@pytest.fixture(scope="session")
def deployed_factorisation_processor(
        docker_available, github_credentials_available, rti_proxy, dor_proxy, session_node
) -> DataObject:
    if not github_credentials_available:
        yield DataObject(
            obj_id='dummy',
            c_hash='dummy',
            data_type='dummy',
            data_format='dummy',
            created=DataObject.CreationDetails(timestamp=0, creators_iid=[]),
            owner_iid='dummy',
            access_restricted=False,
            access=[],
            tags={},
            last_accessed=0,
            custodian=None,
            content_encrypted=False,
            license=DataObject.License(by=False, sa=False, nc=False, nd=False),
            recipe=None
        )
    else:
        # add test processor
        meta = add_test_processor(
            dor_proxy, session_node.keystore, 'proc-factorisation', 'examples/prime/factorisation'
        )
        proc_id = meta.obj_id

        if not docker_available:
            yield meta

        else:
            # deploy it
            rti_proxy.deploy(proc_id, session_node.keystore)
            while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_DEPLOY:
                logger.info(f"Waiting for processor to be ready: {proc}")
                time.sleep(1)

            assert(rti_proxy.get_proc(proc_id).state == Processor.State.READY)
            logger.info(f"Processor deployed: {proc}")

            yield meta

            # undeploy it
            rti_proxy.undeploy(proc_id, session_node.keystore)
            try:
                while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_UNDEPLOY:
                    logger.info(f"Waiting for processor to be ready: {proc}")
                    time.sleep(1)
            except Exception as e:
                print(e)

            logger.info(f"Processor undeployed: {proc}")


@pytest.fixture(scope="session")
def deployed_factor_search_processor(
        docker_available, github_credentials_available, rti_proxy, dor_proxy, session_node
) -> DataObject:
    if not github_credentials_available:
        yield DataObject(
            obj_id='dummy',
            c_hash='dummy',
            data_type='dummy',
            data_format='dummy',
            created=DataObject.CreationDetails(timestamp=0, creators_iid=[]),
            owner_iid='dummy',
            access_restricted=False,
            access=[],
            tags={},
            last_accessed=0,
            custodian=None,
            content_encrypted=False,
            license=DataObject.License(by=False, sa=False, nc=False, nd=False),
            recipe=None
        )
    else:
        # add test processor
        meta = add_test_processor(
            dor_proxy, session_node.keystore, 'proc-factor-search', 'examples/prime/factor_search'
        )
        proc_id = meta.obj_id

        if not docker_available:
            yield meta

        else:
            # deploy it
            rti_proxy.deploy(proc_id, session_node.keystore)
            while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_DEPLOY:
                logger.info(f"Waiting for processor to be ready: {proc}")
                time.sleep(1)

            assert(rti_proxy.get_proc(proc_id).state == Processor.State.READY)
            logger.info(f"Processor deployed: {proc}")

            yield meta

            # undeploy it
            rti_proxy.undeploy(proc_id, session_node.keystore)
            try:
                while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_UNDEPLOY:
                    logger.info(f"Waiting for processor to be ready: {proc}")
                    time.sleep(1)
            except Exception as e:
                print(e)

            logger.info(f"Processor undeployed: {proc}")


def test_factor_search_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_factor_search_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_factor_search_processor.obj_id
    owner = session_node.keystore

    # submit the job
    job = rti_proxy.submit_job(proc_id, [
        Task.InputValue.model_validate({
            'name': 'parameters', 'type': 'value', 'value': {
                'start': 2,
                'end': 100,
                'number': 100
            }
        }),
    ], [
        Task.Output.model_validate({
            'name': 'result',
            'owner_iid': owner.identity.id,
            'restricted_access': False,
            'content_encrypted': False,
            'target_node_iid': None
        })
    ], owner, budget=Task.Budget(vcpus=1, memory=1024))
    assert (job is not None)

    job_id = job.id

    while True:
        try:
            status: JobStatus = rti_proxy.get_job_status(job_id, owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert (status is not None)

            if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                break

        except Exception:
            pass

        time.sleep(1)

    # check if we have an object id for output object 'c'
    assert ('result' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        result: Result = Result.model_validate(result)

    # print the result
    print(result.factors)
    assert (result.factors == [2, 4, 5, 10, 20, 25, 50])


def test_factorisation_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_factorisation_processor, deployed_factor_search_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_factorisation_processor.obj_id
    owner = session_node.keystore

    # submit the job
    job = rti_proxy.submit_job(proc_id, [
        Task.InputValue.model_validate({
            'name': 'parameters', 'type': 'value', 'value': {
                'N': 100,
                'num_sub_jobs': 2
            }
        }),
    ], [
        Task.Output.model_validate({
            'name': 'result',
            'owner_iid': owner.identity.id,
            'restricted_access': False,
            'content_encrypted': False,
            'target_node_iid': None
        })
    ], owner, budget=Task.Budget(vcpus=1, memory=1024))
    assert (job is not None)

    job_id = job.id

    while True:
        try:
            status: JobStatus = rti_proxy.get_job_status(job_id, owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert (status is not None)

            if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                break

        except Exception:
            pass

        time.sleep(1)

    # check if we have an object id for output object 'c'
    assert ('result' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        result: Result = Result.model_validate(result)

    # print the result
    print(result.factors)
    assert (result.factors == [2, 4, 5, 10, 20, 25, 50])
