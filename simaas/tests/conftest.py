import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import traceback
from typing import List, Tuple

import pytest
from dotenv import load_dotenv

from simaas.cli.cmd_proc_builder import clone_repository
from simaas.core.helpers import get_timestamp_now
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.api import DORProxy
from simaas.dor.schemas import ProcessorDescriptor, GitProcessorPointer, DataObject
from simaas.helpers import determine_local_ip, PortMaster
from simaas.node.base import Node
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Processor

load_dotenv()

REPOSITORY_URL = 'https://github.com/sec-digital-twin-lab/sim-aas-middleware'
REPOSITORY_COMMIT_ID = '518133993da11f09decd17d7102ae4e680bb8272'

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
        print("Docker not available!")
        return False


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
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)

        _node = DefaultNode.create(
            keystore=session_keystore, storage_path=tempdir,
            p2p_address=p2p_address, rest_address=rest_address, boot_node_address=rest_address,
            enable_db=True, enable_dor=True, enable_rti=True,
            retain_job_history=True, strict_deployment=False, job_concurrency=True
        )

        yield _node

        _node.shutdown()


def add_test_processor(dor: DORProxy, keystore: Keystore) -> DataObject:
    org = 'sec-digital-twin-lab'
    repo_name = 'sim-aas-middleware'
    repo_url = f'https://github.com/{org}/{repo_name}'
    proc_name = 'example-processor'
    proc_path = 'examples/adapters/proc_example'
    image_name = f'{org}/{repo_name}/{proc_name}:{REPOSITORY_COMMIT_ID}'

    # does it exist in DOR? if not, build and add it
    result = dor.search(data_type='ProcessorDockerImage')
    existing = [obj for obj in result if obj.tags['image_name'] == image_name]
    if not existing:
        with tempfile.TemporaryDirectory() as tempdir:
            # clone the repository and checkout the specified commit
            repo_path = os.path.join(tempdir, 'repository')
            commit_timestamp = clone_repository(repo_url, repo_path, commit_id=REPOSITORY_COMMIT_ID)

            # read the processor descriptor
            descriptor_path = os.path.join(repo_path, proc_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                # noinspection PyTypeChecker
                descriptor = ProcessorDescriptor.model_validate(json.load(f))

            # store the GPP information in a file
            gpp_path = os.path.join(tempdir, 'gpp.json')
            with open(gpp_path, 'w') as f:
                gpp = GitProcessorPointer(repository=repo_url, commit_id=REPOSITORY_COMMIT_ID, proc_path=proc_path,
                                          proc_descriptor=descriptor)
                # noinspection PyTypeChecker
                json.dump(gpp.model_dump(), f)

            # upload to DOR
            meta = dor.add_data_object(gpp_path, keystore.identity, False, False, 'ProcessorDockerImage', 'json',
                                       tags=[
                                           DataObject.Tag(key='repository', value=repo_url),
                                           DataObject.Tag(key='commit_id', value=REPOSITORY_COMMIT_ID),
                                           DataObject.Tag(key='commit_timestamp', value=commit_timestamp),
                                           DataObject.Tag(key='proc_path', value=proc_path),
                                           DataObject.Tag(key='proc_descriptor', value=descriptor.model_dump()),
                                           DataObject.Tag(key='image_name', value=image_name)
                                       ])
            os.remove(gpp_path)

            existing.append(meta)

    return existing[0]


@pytest.fixture(scope="session")
def deployed_test_processor(
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
        meta = add_test_processor(dor_proxy, session_node.keystore)
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

    def create_nodes(self, keystores: List[Keystore], perform_join: bool = True, enable_rest: bool = False) -> List[Node]:
        nodes = []
        for i, keystore in enumerate(keystores):
            nodes.append(self.get_node(keystore, enable_rest=enable_rest))

            if perform_join and i > 0:
                nodes[i].join_network(nodes[0].rest.address())
                time.sleep(2)

        return nodes

    def generate_random_file(self, filename: str, size: int) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'wb') as f:
            f.write(os.urandom(int(size)))
        return path

    def generate_zero_file(self, filename: str, size: int) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'wb') as f:
            f.write(b"\0" * int(size))
        return path

    def create_file_with_content(self, filename: str, content: str) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def get_node(self, keystore: Keystore, enable_rest: bool = False, use_dor: bool = True, use_rti: bool = True,
                 retain_job_history: bool = True, strict_deployment: bool = False, job_concurrency: bool = False,
                 wd_path: str = None) -> Node:
        name = keystore.identity.id
        if name in self.nodes:
            return self.nodes[name]

        p2p_address: str = PortMaster.generate_p2p_address(self.host)
        rest_address: Tuple[str, int] = PortMaster.generate_rest_address(self.host)

        storage_path = os.path.join(wd_path if wd_path else self.testing_dir, name)
        os.makedirs(storage_path, exist_ok=True)

        # create node and startup services
        node = DefaultNode(keystore, storage_path, enable_db=True, enable_dor=use_dor, enable_rti=use_rti,
                           retain_job_history=retain_job_history if use_rti else None,
                           strict_deployment=strict_deployment if use_rti else None,
                           job_concurrency=job_concurrency if use_rti else None)
        node.startup(p2p_address, rest_address=rest_address if enable_rest else None)
        time.sleep(2)

        self.nodes[name] = node

        return node

    def resume_node(self, name: str, enable_rest: bool = False, use_dor: bool = True, use_rti: bool = True,
                    retain_job_history: bool = True, strict_deployment: bool = False) -> Node:
        if name in self.nodes:
            return self.nodes[name]

        else:
            p2p_address = PortMaster.generate_p2p_address(self.host)
            rest_address = PortMaster.generate_rest_address(self.host)

            storage_path = os.path.join(self.testing_dir, name)
            if not os.path.isdir(storage_path):
                raise RuntimeError(f"no storage path found to resume node at {storage_path}")

            # infer the keystore id
            keystore = None
            for filename in os.listdir(storage_path):
                if filename.endswith('.json') and len(filename) == 69:
                    keystore = Keystore.from_file(os.path.join(storage_path, filename), 'password')
                    break

            # create node and startup services
            node = DefaultNode(keystore, storage_path, enable_db=True, enable_dor=use_dor, enable_rti=use_rti,
                               retain_job_history=retain_job_history if use_rti else None,
                               strict_deployment=strict_deployment if use_rti else None,
                               job_concurrency=False)
            node.startup(p2p_address, rest_address=rest_address if enable_rest else None)

            self.nodes[name] = node
            return node


def generate_random_file(path: str, size: int) -> str:
    with open(path, 'wb') as f:
        f.write(os.urandom(int(size)))
    return path
