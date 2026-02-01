"""RTI (Runtime Infrastructure) test fixtures."""

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Type

import pytest

from simaas.core.async_helpers import run_coro_safely

import docker

from simaas.cli.cmd_image import build_processor_image
from simaas.core.helpers import get_timestamp_now
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.dor.api import DORProxy
from simaas.dor.schemas import ProcessorDescriptor, GitProcessorPointer, DataObject
from simaas.helpers import docker_export_image
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Processor, ProcessorVolume

# Import plugin classes
from simaas.plugins.builtins.dor_fs import FilesystemDORService
from simaas.plugins.builtins.rti_docker import DockerRTIService
from simaas.plugins.builtins.rti_aws import AWSRTIService

from simaas.tests.fixture_core import CURRENT_COMMIT_ID

log = get_logger('tests.fixtures.rti', 'test')

# Constants
REPOSITORY_URL = 'https://github.com/sec-digital-twin-lab/sim-aas-middleware'
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Processor paths
PROC_ABC_PATH = "examples/simple/abc"
PROC_PING_PATH = "examples/simple/ping"
PROC_ROOM_PATH = "examples/cosim/room"
PROC_THERMOSTAT_PATH = "examples/cosim/thermostat"
PROC_FACTORISATION_PATH = "examples/prime/factorisation"
PROC_FACTOR_SEARCH_PATH = "examples/prime/factor_search"


class RTIBackend(Enum):
    """Enumeration of available RTI backend types."""
    DOCKER = "docker"
    AWS = "aws"


@dataclass
class RTIBackendConfig:
    """Configuration for an RTI backend."""
    backend: RTIBackend
    plugin_class: Type
    default_memory: int
    volume_config: dict
    skip_reason: str


@dataclass
class RTIContext:
    """Backend-agnostic context for RTI tests.

    Provides all dependencies needed to run RTI tests against either
    the Docker or AWS backend. Tests should use this context instead
    of directly using backend-specific fixtures.
    """
    backend: str                          # "docker" or "aws"
    session_node: 'DefaultNode'           # The node instance
    rti_proxy: RTIProxy                   # RTI API proxy
    dor_proxy: DORProxy                   # DOR API proxy
    node_db_proxy: NodeDBProxy            # NodeDB API proxy
    deployed_abc_processor: DataObject    # Pre-deployed ABC processor
    deployed_room_processor: DataObject   # Pre-deployed Room processor
    deployed_thermostat_processor: DataObject  # Pre-deployed Thermostat processor
    default_memory: int                   # Default memory for tasks (1024 or 2048)

    def get_known_user(self, extra_keystores) -> Keystore:
        """Register and return a known user for authorization tests."""
        keystore = extra_keystores[2]
        self.node_db_proxy.update_identity(keystore.identity)
        return keystore


def check_docker_image_exists(image_name: str) -> bool:
    """Check if a Docker image exists locally.

    Args:
        image_name: Full image name including tag (e.g., 'org/repo/proc-abc:commit_id')

    Returns:
        True if the image exists locally, False otherwise.
    """
    try:
        client = docker.from_env()
        client.images.get(image_name)
        return True
    except docker.errors.ImageNotFound:
        return False
    except docker.errors.DockerException:
        # Docker not available or other error
        return False


class ProcessorDeployment:
    """Context manager for deploying processors in tests.

    Encapsulates the full lifecycle of processor deployment:
    - Docker image existence check (warns if not pre-built by Wave 0)
    - Build via add_test_processor() if needed
    - Deploy to RTI
    - Wait for ready state
    - Undeploy on exit
    - Wait for undeployed state

    Usage:
        with ProcessorDeployment(
            proc_name='proc-abc',
            proc_path=PROC_ABC_PATH,
            dor_proxy=dor_proxy,
            rti_proxy=rti_proxy,
            session_node=session_node,
            docker_available=docker_available,
            volumes=[...]
        ) as meta:
            # meta is the DataObject for the processor
            ...
    """

    def __init__(
        self,
        proc_name: str,
        proc_path: str,
        dor_proxy: DORProxy,
        rti_proxy: RTIProxy,
        session_node: 'DefaultNode',
        docker_available: bool,
        volumes: list = None,
        platform: str = 'linux/amd64'
    ):
        self.proc_name = proc_name
        self.proc_path = proc_path
        self.dor_proxy = dor_proxy
        self.rti_proxy = rti_proxy
        self.session_node = session_node
        self.docker_available = docker_available
        self.volumes = volumes
        self.platform = platform
        self.meta: DataObject = None
        self.proc_id: str = None

    def _get_image_name(self) -> str:
        """Get the full Docker image name for this processor."""
        org = 'sec-digital-twin-lab'
        repo_name = 'sim-aas-middleware'
        return f'{org}/{repo_name}/{self.proc_name}:{CURRENT_COMMIT_ID}'

    def __enter__(self) -> DataObject:
        """Deploy processor, requiring image to be pre-built by Wave 0."""
        image_name = self._get_image_name()

        # Require image to be pre-built by Wave 0
        if self.docker_available and not check_docker_image_exists(image_name):
            raise RuntimeError(
                f"Docker image '{image_name}' not found. "
                "Run Wave 0 (test_cli_image.py) first to build all processor images."
            )

        # Add to DOR (image must already exist)
        self.meta = add_test_processor(
            self.dor_proxy, self.session_node.keystore,
            proc_name=self.proc_name, proc_path=self.proc_path, platform=self.platform
        )
        self.proc_id = self.meta.obj_id

        if not self.docker_available:
            return self.meta

        # Deploy
        self.rti_proxy.deploy(self.proc_id, self.session_node.keystore, volumes=self.volumes)
        wait_for_processor_ready(self.rti_proxy, self.proc_id)
        log.info(f"Processor deployed: {self.proc_id}")

        return self.meta

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Undeploy processor."""
        if not self.docker_available or self.proc_id is None:
            return

        self.rti_proxy.undeploy(self.proc_id, self.session_node.keystore)
        wait_for_processor_undeployed(self.rti_proxy, self.proc_id)
        log.info(f"Processor undeployed: {self.proc_id}")


def add_test_processor(
        dor: DORProxy, keystore: Keystore, proc_name: str, proc_path: str, platform: str = 'linux/amd64'
) -> DataObject:
    """Build and add a processor image to DOR if it doesn't exist.

    Uses an isolated temp copy for gpp.json creation to avoid race conditions.
    """
    import shutil

    org = 'sec-digital-twin-lab'
    repo_name = 'sim-aas-middleware'
    repo_url = f'https://github.com/{org}/{repo_name}'
    image_name = f'{org}/{repo_name}/{proc_name}:{CURRENT_COMMIT_ID}'

    # does it exist in DOR? if not, build and add it
    result = dor.search(data_type='ProcessorDockerImage')
    existing = [obj for obj in result if obj.tags['image_name'] == image_name]
    if not existing:
        with tempfile.TemporaryDirectory() as tempdir:
            # don't clone the repo but use this repo (since it's sim-aas-middleware)
            repo_path = str(BASE_DIR)

            # make full proc path and copy to isolated temp location
            abs_proc_path = os.path.join(repo_path, proc_path)
            temp_proc_path = os.path.join(tempdir, os.path.basename(proc_path))
            shutil.copytree(abs_proc_path, temp_proc_path)

            # read the processor descriptor
            descriptor_path = os.path.join(temp_proc_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                # noinspection PyTypeChecker
                descriptor = ProcessorDescriptor.model_validate(json.load(f))

            # create the GPP descriptor in the temp copy
            gpp: GitProcessorPointer = GitProcessorPointer(
                repository=repo_url,
                commit_id=CURRENT_COMMIT_ID,
                proc_path=proc_path,
                proc_descriptor=descriptor
            )
            gpp_path = os.path.join(temp_proc_path, 'gpp.json')
            with open(gpp_path, 'w') as f:
                json.dump(gpp.model_dump(), f, indent=2)

            # get the credentials
            credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])

            # build the image from the isolated temp copy
            build_processor_image(
                temp_proc_path, os.environ['SIMAAS_REPO_PATH'], image_name, credentials=credentials, platform=platform
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

            existing.append(meta)

    return existing[0]


def wait_for_processor_ready(rti_proxy: RTIProxy, proc_id: str, timeout: float = 120.0) -> Processor:
    """Wait for a processor to become ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        proc = rti_proxy.get_proc(proc_id)
        if proc and proc.state == Processor.State.READY:
            return proc
        # If processor is in a terminal state (FAILED), stop waiting
        if proc and proc.state == Processor.State.FAILED:
            raise RuntimeError(f"Processor {proc_id} failed to deploy")
        time.sleep(1)
    raise TimeoutError(f"Processor {proc_id} did not become ready within {timeout}s")


def wait_for_processor_undeployed(rti_proxy: RTIProxy, proc_id: str, timeout: float = 120.0) -> None:
    """Wait for a processor to be undeployed."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            proc = rti_proxy.get_proc(proc_id)
            # Processor is undeployed when get_proc returns None
            if proc is None:
                return
            # If not in BUSY_UNDEPLOY state, it's either done or failed
            if proc.state != Processor.State.BUSY_UNDEPLOY:
                return
        except Exception:
            # Exception likely means processor not found (undeployed)
            return
        time.sleep(1)


# ==============================================================================
# RTI Proxy Fixture
# ==============================================================================

@pytest.fixture(scope="session")
def rti_proxy(session_node):
    """Session-scoped RTI proxy connected to session_node.

    Provides access to the RTI service for submitting jobs, deploying
    processors, and managing the runtime infrastructure.
    """
    proxy = RTIProxy(session_node.rest.address())
    return proxy


# ==============================================================================
# Deployed Processor Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def deployed_abc_processor(docker_available, rti_proxy, dor_proxy, session_node, session_data_dir) -> DataObject:
    """Session-scoped fixture that deploys the ABC processor."""
    volumes = [ProcessorVolume(name='data_volume', mount_point='/data', read_only=False,
                               reference={'path': session_data_dir})]
    with ProcessorDeployment(
        'proc-abc', PROC_ABC_PATH, dor_proxy, rti_proxy, session_node,
        docker_available, volumes=volumes
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_ping_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    """Session-scoped fixture that deploys the Ping processor."""
    with ProcessorDeployment(
        'proc-ping', PROC_PING_PATH, dor_proxy, rti_proxy, session_node,
        docker_available
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_room_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    """Session-scoped fixture that deploys the Room processor for co-simulation tests."""
    with ProcessorDeployment(
        'proc-room', PROC_ROOM_PATH, dor_proxy, rti_proxy, session_node,
        docker_available
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_thermostat_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    """Session-scoped fixture that deploys the Thermostat processor for co-simulation tests."""
    with ProcessorDeployment(
        'proc-thermostat', PROC_THERMOSTAT_PATH, dor_proxy, rti_proxy, session_node,
        docker_available
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_factorisation_processor(
        docker_available, rti_proxy, dor_proxy, session_node
) -> DataObject:
    """Session-scoped fixture that deploys the Factorisation processor."""
    with ProcessorDeployment(
        'proc-factorisation', PROC_FACTORISATION_PATH, dor_proxy, rti_proxy, session_node,
        docker_available
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_factor_search_processor(
        docker_available, rti_proxy, dor_proxy, session_node
) -> DataObject:
    """Session-scoped fixture that deploys the Factor Search processor."""
    with ProcessorDeployment(
        'proc-factor-search', PROC_FACTOR_SEARCH_PATH, dor_proxy, rti_proxy, session_node,
        docker_available
    ) as meta:
        yield meta


# ==============================================================================
# Docker-Specific Node Fixtures
# ==============================================================================

@pytest.fixture(scope='session')
def docker_non_strict_node(test_context):
    """Docker-based node with non-strict deployment mode.

    Non-strict mode allows any user to deploy and undeploy processors.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("docker_non_strict_node", "no-email-provided", path=tempdir, password="password")
        _node = test_context.get_node(keystore, rti_plugin_class=DockerRTIService, enable_rest=True, strict_deployment=False)
        yield _node


@pytest.fixture(scope='session')
def docker_strict_node(test_context, extra_keystores):
    """Docker-based node with strict deployment mode.

    Strict mode requires the node owner to deploy and undeploy processors.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("docker_strict_node", "no-email-provided", path=tempdir, password="password")
        _node = test_context.get_node(keystore, rti_plugin_class=DockerRTIService, enable_rest=True, strict_deployment=True)
        yield _node


# ==============================================================================
# AWS-Specific Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def ssh_tunnel():
    """Set up an SSH tunnel for AWS connectivity."""
    ssh_host = os.environ.get("SSH_TUNNEL_HOST")
    ssh_user = os.environ.get("SSH_TUNNEL_USER")
    ssh_key_path = os.environ.get("SSH_TUNNEL_KEY_PATH")

    if not ssh_host or not ssh_user or not ssh_key_path:
        pytest.skip("Skipping test: SSH tunnel credentials are missing.")

    # SSH tunnel command (runs in the background)
    ssh_command = [
        "ssh",
        "-N",  # Do not execute remote commands
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-R", "0.0.0.0:5999:localhost:5999",  # Forward remote port 5999 to local port 5999
        "-R", "0.0.0.0:4999:localhost:4999",  # Forward remote port 4999 to local port 4999
        "-i", ssh_key_path,  # Private key authentication
        f"{ssh_user}@{ssh_host}"  # Remote SSH target
    ]

    # Start the SSH tunnel as a subprocess
    process = subprocess.Popen(
        ssh_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    time.sleep(2)

    yield

    process.terminate()
    process.wait()


@pytest.fixture(scope="session")
def aws_session_node(aws_available, ssh_tunnel, session_keystore, session_node):
    """Session-scoped node configured for AWS RTI backend.

    If AWS is not available, falls back to the regular session_node.
    """
    if not aws_available:
        yield session_node

    else:
        # Get the EC2's internal hostname - this is what AWS jobs will use to connect
        # The SSH tunnel forwards from EC2:4999 to localhost:4999
        ssh_host = os.environ.get("SSH_TUNNEL_HOST")
        ssh_user = os.environ.get("SSH_TUNNEL_USER")
        ssh_key_path = os.environ.get("SSH_TUNNEL_KEY_PATH")

        # Get EC2 internal hostname
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-i", ssh_key_path,
             f"{ssh_user}@{ssh_host}", "hostname", "-f"],
            capture_output=True, text=True, timeout=30
        )
        ec2_internal_hostname = result.stdout.strip()
        log.info(f"EC2 internal hostname for custodian: {ec2_internal_hostname}")

        # Set the custodian host so AWS jobs connect to EC2 (via tunnel) not local machine
        os.environ["SIMAAS_CUSTODIAN_HOST"] = ec2_internal_hostname

        with tempfile.TemporaryDirectory() as tempdir:
            rest_address = ('localhost', 5999)
            p2p_address = "tcp://localhost:4999"

            _node = DefaultNode.create(
                keystore=session_keystore, storage_path=tempdir,
                p2p_address=p2p_address, rest_address=rest_address,
                enable_db=True, dor_plugin_class=FilesystemDORService, rti_plugin_class=AWSRTIService,
                retain_job_history=True, strict_deployment=False
            )

            # join the network using the session_node's REST address
            run_coro_safely(_node.join_network(session_node.rest.address()))

            yield _node

            _node.shutdown()

            # Clean up environment variable
            if "SIMAAS_CUSTODIAN_HOST" in os.environ:
                del os.environ["SIMAAS_CUSTODIAN_HOST"]


@pytest.fixture(scope="session")
def aws_dor_proxy(aws_session_node):
    """DOR proxy for AWS session node."""
    proxy = DORProxy(aws_session_node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def aws_rti_proxy(aws_session_node):
    """RTI proxy for AWS session node."""
    proxy = RTIProxy(aws_session_node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def aws_node_db_proxy(aws_session_node):
    """NodeDB proxy for AWS session node."""
    proxy = NodeDBProxy(aws_session_node.rest.address())
    return proxy


@pytest.fixture(scope='session')
def aws_non_strict_node(aws_available, test_context, session_node):
    """AWS-based node with non-strict deployment mode."""
    if not aws_available:
        yield session_node

    else:
        with tempfile.TemporaryDirectory() as tempdir:
            keystore = Keystore.new("aws_non_strict_node", "no-email-provided", path=tempdir, password="password")
            _node = test_context.get_node(keystore, rti_plugin_class=AWSRTIService, enable_rest=True, strict_deployment=False)
            yield _node


@pytest.fixture(scope='session')
def aws_strict_node(aws_available, test_context, extra_keystores, session_node):
    """AWS-based node with strict deployment mode."""
    if not aws_available:
        yield session_node

    else:
        with tempfile.TemporaryDirectory() as tempdir:
            keystore = Keystore.new("aws_strict_node", "no-email-provided", path=tempdir, password="password")
            _node = test_context.get_node(keystore, rti_plugin_class=AWSRTIService, enable_rest=True, strict_deployment=True)
            yield _node


@pytest.fixture(scope="session")
def aws_deployed_abc_processor(
        docker_available, aws_available, aws_rti_proxy, aws_dor_proxy, aws_session_node
) -> DataObject:
    """AWS-deployed ABC processor fixture.

    Deploys the ABC processor on the AWS RTI backend with EFS volume configuration.
    """
    meta = add_test_processor(
        aws_dor_proxy, aws_session_node.keystore, 'proc-abc', PROC_ABC_PATH, 'linux/amd64'
    )
    proc_id = meta.obj_id

    if not docker_available or not aws_available:
        yield meta

    else:
        # deploy it with EFS volume
        aws_rti_proxy.deploy(proc_id, aws_session_node.keystore, volumes=[
            ProcessorVolume(name='data_volume', mount_point='/data', read_only=False, reference={
                'efsFileSystemId': 'fs-0bf7f8e5a6ae69397',
                'rootDirectory': '/',
                'transitEncryption': 'ENABLED'
            })
        ])

        wait_for_processor_ready(aws_rti_proxy, proc_id)
        log.info(f"AWS Processor deployed: {proc_id}")

        yield meta

        # undeploy it
        aws_rti_proxy.undeploy(proc_id, aws_session_node.keystore)
        wait_for_processor_undeployed(aws_rti_proxy, proc_id)
        log.info(f"AWS Processor undeployed: {proc_id}")


@pytest.fixture(scope="session")
def aws_deployed_room_processor(
        docker_available, aws_available, aws_rti_proxy, aws_dor_proxy, aws_session_node
) -> DataObject:
    """AWS-deployed Room processor for co-simulation tests."""
    meta = add_test_processor(
        aws_dor_proxy, aws_session_node.keystore, 'proc-room', PROC_ROOM_PATH
    )
    proc_id = meta.obj_id

    if not docker_available or not aws_available:
        yield meta

    else:
        aws_rti_proxy.deploy(proc_id, aws_session_node.keystore)
        wait_for_processor_ready(aws_rti_proxy, proc_id)
        log.info(f"AWS Processor deployed: {proc_id}")

        yield meta

        aws_rti_proxy.undeploy(proc_id, aws_session_node.keystore)
        wait_for_processor_undeployed(aws_rti_proxy, proc_id)
        log.info(f"AWS Processor undeployed: {proc_id}")


@pytest.fixture(scope="session")
def aws_deployed_thermostat_processor(
        docker_available, aws_available, aws_rti_proxy, aws_dor_proxy, aws_session_node
) -> DataObject:
    """AWS-deployed Thermostat processor for co-simulation tests."""
    meta = add_test_processor(
        aws_dor_proxy, aws_session_node.keystore, 'proc-thermostat', PROC_THERMOSTAT_PATH
    )
    proc_id = meta.obj_id

    if not docker_available or not aws_available:
        yield meta

    else:
        aws_rti_proxy.deploy(proc_id, aws_session_node.keystore)
        wait_for_processor_ready(aws_rti_proxy, proc_id)
        log.info(f"AWS Processor deployed: {proc_id}")

        yield meta

        aws_rti_proxy.undeploy(proc_id, aws_session_node.keystore)
        wait_for_processor_undeployed(aws_rti_proxy, proc_id)
        log.info(f"AWS Processor undeployed: {proc_id}")


# ==============================================================================
# Known User Fixture
# ==============================================================================

@pytest.fixture()
def known_user(extra_keystores, node_db_proxy):
    """Function-scoped fixture providing a known user for authorization tests."""
    _keystore = extra_keystores[2]
    node_db_proxy.update_identity(_keystore.identity)
    return _keystore


@pytest.fixture()
def aws_known_user(extra_keystores, aws_node_db_proxy):
    """Function-scoped fixture providing a known user for AWS authorization tests."""
    _keystore = extra_keystores[2]
    aws_node_db_proxy.update_identity(_keystore.identity)
    return _keystore


# ==============================================================================
# Parameterized RTI Context Fixture
# ==============================================================================

@pytest.fixture(scope="session", params=["docker", "aws"])
def rti_context(
    request,
    docker_available,
    aws_available,
    # Docker fixtures
    session_node,
    rti_proxy,
    dor_proxy,
    node_db_proxy,
    deployed_abc_processor,
    deployed_room_processor,
    deployed_thermostat_processor,
    # AWS fixtures
    aws_session_node,
    aws_rti_proxy,
    aws_dor_proxy,
    aws_node_db_proxy,
    aws_deployed_abc_processor,
    aws_deployed_room_processor,
    aws_deployed_thermostat_processor,
) -> RTIContext:
    """Parameterized fixture providing RTI context for either Docker or AWS backend.

    Tests using this fixture will automatically run twice: once for Docker
    and once for AWS. Test output will show as test_name[docker] and test_name[aws].
    """
    if request.param == "docker":
        if not docker_available:
            pytest.skip("Docker is not available")
        return RTIContext(
            backend="docker",
            session_node=session_node,
            rti_proxy=rti_proxy,
            dor_proxy=dor_proxy,
            node_db_proxy=node_db_proxy,
            deployed_abc_processor=deployed_abc_processor,
            deployed_room_processor=deployed_room_processor,
            deployed_thermostat_processor=deployed_thermostat_processor,
            default_memory=1024,
        )
    else:  # aws
        if not aws_available:
            pytest.skip("AWS is not available")
        return RTIContext(
            backend="aws",
            session_node=aws_session_node,
            rti_proxy=aws_rti_proxy,
            dor_proxy=aws_dor_proxy,
            node_db_proxy=aws_node_db_proxy,
            deployed_abc_processor=aws_deployed_abc_processor,
            deployed_room_processor=aws_deployed_room_processor,
            deployed_thermostat_processor=aws_deployed_thermostat_processor,
            default_memory=2048,
        )
