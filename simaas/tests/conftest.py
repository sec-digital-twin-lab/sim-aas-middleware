import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Union

import pytest
from dotenv import load_dotenv

from examples.cosim.room.processor import RoomProcessor
from examples.cosim.thermostat.processor import ThermostatProcessor
from simaas.cli.cmd_image import build_processor_image
from simaas.core.processor import ProcessorBase, ProgressListener

from examples.prime.factor_search.processor import ProcessorFactorSearch
from examples.prime.factorisation.processor import ProcessorFactorisation
from simaas.namespace.api import Namespace
from simaas.nodedb.schemas import NodeInfo

from simaas.core.helpers import get_timestamp_now, hash_json_object, generate_random_string
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.api import DORProxy, DORInterface
from simaas.dor.schemas import ProcessorDescriptor, GitProcessorPointer, DataObject, DataObjectProvenance, \
    DataObjectRecipe, DORStatistics
from simaas.helpers import determine_local_ip, PortMaster, docker_export_image
from simaas.node.base import Node
from simaas.node.default import DefaultNode, DORType, RTIType
from simaas.nodedb.api import NodeDBProxy
from simaas.rti.api import RTIProxy, RTIInterface
from simaas.rti.aws import get_default_aws_config
from simaas.rti.schemas import Processor, JobStatus, Task, Job, Severity, BatchStatus, ProcessorVolume

load_dotenv()

REPOSITORY_URL = 'https://github.com/sec-digital-twin-lab/sim-aas-middleware'
REPOSITORY_COMMIT_ID = 'b9e729d94e5ac55ff04eefef56d199396cdc1ba0'
PROC_ABC_PATH = "examples/simple/abc"
PROC_PING_PATH = "examples/simple/ping"

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# deactivate annoying DEBUG messages by multipart
logging.getLogger('multipart.multipart').setLevel(logging.WARNING)
logging.getLogger('python_multipart.multipart').setLevel(logging.WARNING)

logger = Logging.get('tests.conftest')


@pytest.fixture(scope='session')
def test_context():
    context = TestContext()
    context.initialise()
    yield context
    context.cleanup()


@pytest.fixture(scope="session")
def docker_available():
    try:
        subprocess.run(['docker', 'info'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def aws_available():
    return get_default_aws_config() is not None


@pytest.fixture(scope="session")
def github_credentials_available():
    if all(key in os.environ for key in ['GITHUB_USERNAME', 'GITHUB_TOKEN']):
        return True
    else:
        print("GitHub credentials not available!")
        return False


@pytest.fixture(scope="session")
def session_keystore(github_credentials_available):
    with tempfile.TemporaryDirectory() as tempdir:
        _keystore = Keystore.new("keystore1", "no-email-provided", path=tempdir, password="password")
        if github_credentials_available:
            _keystore.github_credentials.update(
                REPOSITORY_URL,
                GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
            )
        yield _keystore


@pytest.fixture(scope="session")
def temp_directory():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


@pytest.fixture(scope="session")
def dor_proxy(session_node):
    proxy = DORProxy(session_node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def node_db_proxy(session_node):
    proxy = NodeDBProxy(session_node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def rti_proxy(session_node):
    proxy = RTIProxy(session_node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def extra_keystores(github_credentials_available):
    keystores = []
    with tempfile.TemporaryDirectory() as tempdir:
        for i in range(3):
            keystore = Keystore.new(f"keystore-{i}", "no-email-provided", path=tempdir, password="password")
            if github_credentials_available:
                keystore.github_credentials.update(
                    REPOSITORY_URL,
                    GithubCredentials(
                        login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN']
                    )
                )
            keystores.append(keystore)
        yield keystores


@pytest.fixture(scope="session")
def session_node(session_keystore):
    with tempfile.TemporaryDirectory() as tempdir:
        local_ip = determine_local_ip()

        # create node0
        datastore0 = os.path.join(tempdir, 'session_node0')
        rest_address0 = PortMaster.generate_rest_address(host=local_ip)
        p2p_address0 = PortMaster.generate_p2p_address(host=local_ip)
        _node0 = DefaultNode.create(
            keystore=session_keystore, storage_path=datastore0,
            p2p_address=p2p_address0, rest_address=rest_address0, boot_node_address=rest_address0,
            enable_db=True, dor_type=DORType.BASIC, rti_type=RTIType.DOCKER,
            retain_job_history=False, strict_deployment=False
        )

        # create node1 to ensure there is a network (of 2 nodes). the rationale here is that some errors may only
        # occur if there is a network of nodes. so even though we only return one node as 'session node' that node
        # is part of a network.
        keystore1 = Keystore.new('session_node1')
        datastore1 = os.path.join(tempdir, 'session_node1')
        rest_address1 = PortMaster.generate_rest_address(host=local_ip)
        p2p_address1 = PortMaster.generate_p2p_address(host=local_ip)
        _node1 = DefaultNode.create(
            keystore=keystore1, storage_path=datastore1,
            p2p_address=p2p_address1, rest_address=rest_address1, boot_node_address=rest_address0,
            enable_db=True, dor_type=DORType.BASIC, rti_type=RTIType.NONE,
            retain_job_history=False, strict_deployment=False
        )

        network = _node0.db.get_network()
        assert len(network) == 2

        yield _node0

        _node0.shutdown(leave_network=False)
        _node1.shutdown(leave_network=False)


@pytest.fixture(scope="session")
def session_data_dir(session_keystore):
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def add_test_processor(
        dor: DORProxy, keystore: Keystore, proc_name: str, proc_path: str, platform: str = 'linux/amd64'
) -> DataObject:
    org = 'sec-digital-twin-lab'
    repo_name = 'sim-aas-middleware'
    repo_url = f'https://github.com/{org}/{repo_name}'
    image_name = f'{org}/{repo_name}/{proc_name}:{REPOSITORY_COMMIT_ID}'

    # does it exist in DOR? if not, build and add it
    result = dor.search(data_type='ProcessorDockerImage')
    existing = [obj for obj in result if obj.tags['image_name'] == image_name]
    if not existing:
        with tempfile.TemporaryDirectory() as tempdir:
            # don't clone the repo but use this repo (since it's sim-aas-middleware)
            repo_path = os.path.abspath(os.path.join(os.getcwd(), '..', '..'))

            # make full proc path
            abs_proc_path = os.path.join(repo_path, proc_path)

            # read the processor descriptor
            descriptor_path = os.path.join(abs_proc_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                # noinspection PyTypeChecker
                descriptor = ProcessorDescriptor.model_validate(json.load(f))

            # create the GPP descriptor
            gpp: GitProcessorPointer = GitProcessorPointer(
                repository=repo_url,
                commit_id=REPOSITORY_COMMIT_ID,
                proc_path=proc_path,
                proc_descriptor=descriptor
            )
            gpp_path = os.path.join(abs_proc_path, 'gpp.json')
            with open(gpp_path, 'w') as f:
                json.dump(gpp.model_dump(), f, indent=2)

            # get the credentials
            credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])

            # build the image
            build_processor_image(
                abs_proc_path, os.environ['SIMAAS_REPO_PATH'], image_name, credentials=credentials, platform=platform
            )

            # export the image
            image_path = os.path.join(tempdir, 'pdi.tar')
            docker_export_image(image_name, image_path)

            # upload to DOR
            meta = dor.add_data_object(image_path, keystore.identity, False, False, 'ProcessorDockerImage', 'tar',
                                       tags=[
                                           DataObject.Tag(key='repository', value=gpp.repository),
                                           DataObject.Tag(key='commit_id', value=gpp.commit_id),
                                           DataObject.Tag(key='commit_timestamp', value=get_timestamp_now()),
                                           DataObject.Tag(key='proc_path', value=gpp.proc_path),
                                           DataObject.Tag(key='proc_descriptor', value=gpp.proc_descriptor.model_dump()),
                                           DataObject.Tag(key='image_name', value=image_name)
                                       ])
            os.remove(gpp_path)

            existing.append(meta)

    return existing[0]


@pytest.fixture(scope="session")
def deployed_abc_processor(docker_available, rti_proxy, dor_proxy, session_node, session_data_dir) -> DataObject:
    # add test processor
    meta = add_test_processor(
        dor_proxy, session_node.keystore, proc_name='proc-abc', proc_path=PROC_ABC_PATH, platform='linux/amd64'
    )
    proc_id = meta.obj_id

    if not docker_available:
        yield meta

    else:
        # deploy it
        rti_proxy.deploy(proc_id, session_node.keystore, volumes=[
            ProcessorVolume(name='data_volume', mount_point='/data', read_only=False, reference={
                'path': session_data_dir
            })
        ])

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
def deployed_ping_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    # add test processor
    meta = add_test_processor(
        dor_proxy, session_node.keystore, proc_name='proc-ping', proc_path=PROC_PING_PATH, platform='linux/amd64'
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


class TestContext:
    def __init__(self):
        self._temp_testing_dir = os.path.join(os.environ['HOME'], 'testing')
        self.testing_dir = os.path.join(self._temp_testing_dir, str(get_timestamp_now()))
        self.host = "127.0.0.1"
        self.nodes = dict()
        self.proxies = dict()

    def initialise(self) -> None:
        # the testing directory gets deleted after the test is completed. if it already exists (unlikely) then
        # we abort in order not to end up deleting something that shouldn't be deleted.
        try:
            # create an empty working directory
            os.makedirs(self.testing_dir)
        except OSError as e:
            raise Exception(f"path to working directory for testing '{self.testing_dir}' already exists!") from e

    def cleanup(self) -> None:
        for name in self.nodes:
            logger.info(f"stopping node '{name}'")
            node = self.nodes[name]
            node.shutdown(leave_network=False)

        try:
            shutil.rmtree(self._temp_testing_dir)
        except OSError as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"exception during cleanup() -> {e} {trace}")

    def create_keystores(self, n: int, use_credentials: bool = False) -> List[Keystore]:
        keystores = []
        for i in range(n):
            keystore = Keystore.new(f"keystore_{i}", "no-email-provided", path=self.testing_dir, password=f"password_{i}")
            keystores.append(keystore)

            # update keystore credentials (if applicable)
            if use_credentials:
                keystore.github_credentials.update(
                    REPOSITORY_URL,
                    GithubCredentials(login=os.environ['GITHUB_USERNAME'],
                                      personal_access_token=os.environ['GITHUB_TOKEN'])
                )

        return keystores

    def generate_random_file(self, filename: str, size: int) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'wb') as f:
            f.write(os.urandom(int(size)))
        return path

    def create_file_with_content(self, filename: str, content: str) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def get_node(self, keystore: Keystore, enable_rest: bool = False, dor_type: DORType = DORType.BASIC,
                 rti_type: RTIType = RTIType.DOCKER, retain_job_history: bool = True, strict_deployment: bool = False,
                 wd_path: str = None) -> Node:
        name = keystore.identity.id
        if name in self.nodes:
            return self.nodes[name]

        p2p_address: str = PortMaster.generate_p2p_address(self.host)
        rest_address: Tuple[str, int] = PortMaster.generate_rest_address(self.host)

        storage_path = os.path.join(wd_path if wd_path else self.testing_dir, name)
        os.makedirs(storage_path, exist_ok=True)

        # create node and startup services
        node = DefaultNode(keystore, storage_path, enable_db=True, dor_type=dor_type, rti_type=rti_type,
                           retain_job_history=retain_job_history if rti_type != RTIType.NONE else None,
                           strict_deployment=strict_deployment if rti_type != RTIType.NONE else None)
        node.startup(p2p_address, rest_address=rest_address if enable_rest else None)
        time.sleep(2)

        self.nodes[name] = node

        return node


def generate_random_file(path: str, size: int) -> str:
    with open(path, 'wb') as f:
        f.write(os.urandom(int(size)))
    return path


class DummyProgressListener(ProgressListener):
    def __init__(
            self, job_path: str, status: JobStatus, dor: DORInterface, expected_messages: Optional[List[str]] = None
    ):
        self._job_path = job_path
        self._status = status
        self._dor = dor
        self._expected_messages = expected_messages

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
        if self._expected_messages is not None:
            assert message == self._expected_messages[0]
            self._expected_messages.pop(0)


class DummyNamespace(Namespace):
    class DummyDOR(DORInterface):
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
            self._status[job_id].state =  JobStatus.State.CANCELLED

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
    namespace = DummyNamespace()
    yield namespace
