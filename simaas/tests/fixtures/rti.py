"""RTI (Runtime Infrastructure) test fixtures.

This module contains:
- RTIBackend enum for backend type identification
- RTIBackendConfig dataclass for backend configuration
- Parameterized rti_backend fixture for testing across backends
- RTI proxy fixtures
- Deployed processor fixtures
- Helper functions for processor deployment
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Type, Optional

import pytest

from simaas.cli.cmd_image import build_processor_image
from simaas.core.helpers import get_timestamp_now
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.api import DORProxy
from simaas.dor.schemas import ProcessorDescriptor, GitProcessorPointer, DataObject
from simaas.helpers import docker_export_image, determine_local_ip, PortMaster
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Processor, ProcessorVolume

# Import plugin classes
from plugins.dor_default import DefaultDORService
from plugins.rti_docker import DefaultRTIService
from plugins.rti_aws import AWSRTIService

logger = Logging.get('tests.fixtures.rti')

# Constants
REPOSITORY_URL = 'https://github.com/sec-digital-twin-lab/sim-aas-middleware'
REPOSITORY_COMMIT_ID = 'b9e729d94e5ac55ff04eefef56d199396cdc1ba0'
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Processor paths
PROC_ABC_PATH = "examples/simple/abc"
PROC_PING_PATH = "examples/simple/ping"
PROC_ROOM_PATH = "examples/cosim/room"
PROC_THERMOSTAT_PATH = "examples/cosim/thermostat"


class RTIBackend(Enum):
    """Enumeration of available RTI backend types."""
    DOCKER = "docker"
    AWS = "aws"


@dataclass
class RTIBackendConfig:
    """Configuration for an RTI backend.

    Attributes:
        backend: The type of backend (DOCKER or AWS)
        plugin_class: The RTI service plugin class to use
        default_memory: Default memory allocation in MB
        volume_config: Backend-specific volume configuration
        skip_reason: Message to display when skipping tests for this backend
    """
    backend: RTIBackend
    plugin_class: Type
    default_memory: int
    volume_config: dict
    skip_reason: str


def add_test_processor(
        dor: DORProxy, keystore: Keystore, proc_name: str, proc_path: str, platform: str = 'linux/amd64'
) -> DataObject:
    """Build and add a processor image to DOR if it doesn't exist.

    Args:
        dor: DOR proxy to add the processor to
        keystore: Keystore for authentication
        proc_name: Name of the processor
        proc_path: Path to the processor source code
        platform: Target platform for the image (default: linux/amd64)

    Returns:
        DataObject representing the processor in DOR
    """
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
            repo_path = str(BASE_DIR)

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


def wait_for_processor_ready(rti_proxy: RTIProxy, proc_id: str, timeout: float = 120.0) -> Processor:
    """Wait for a processor to become ready.

    Args:
        rti_proxy: RTI proxy to check processor status
        proc_id: Processor ID to wait for
        timeout: Maximum wait time in seconds

    Returns:
        Processor object in READY state

    Raises:
        TimeoutError: If processor doesn't become ready within timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        proc = rti_proxy.get_proc(proc_id)
        if proc and proc.state == Processor.State.READY:
            return proc
        if proc and proc.state not in [Processor.State.BUSY_DEPLOY, Processor.State.NOT_DEPLOYED]:
            break
        time.sleep(1)
    raise TimeoutError(f"Processor {proc_id} did not become ready within {timeout}s")


def wait_for_processor_undeployed(rti_proxy: RTIProxy, proc_id: str, timeout: float = 120.0) -> None:
    """Wait for a processor to be undeployed.

    Args:
        rti_proxy: RTI proxy to check processor status
        proc_id: Processor ID to wait for
        timeout: Maximum wait time in seconds
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            proc = rti_proxy.get_proc(proc_id)
            if proc is None or proc.state == Processor.State.NOT_DEPLOYED:
                return
            if proc.state != Processor.State.BUSY_UNDEPLOY:
                return
        except Exception:
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
    """Session-scoped fixture that deploys the ABC processor.

    The ABC processor is a simple arithmetic processor that adds two inputs.
    It's deployed with a data volume for testing volume mounting.

    Yields:
        DataObject representing the deployed processor
    """
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

        wait_for_processor_ready(rti_proxy, proc_id)
        logger.info(f"Processor deployed: {proc_id}")

        yield meta

        # undeploy it
        rti_proxy.undeploy(proc_id, session_node.keystore)
        wait_for_processor_undeployed(rti_proxy, proc_id)
        logger.info(f"Processor undeployed: {proc_id}")


@pytest.fixture(scope="session")
def deployed_ping_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    """Session-scoped fixture that deploys the Ping processor.

    The Ping processor is a simple processor that echoes back inputs.

    Yields:
        DataObject representing the deployed processor
    """
    meta = add_test_processor(
        dor_proxy, session_node.keystore, proc_name='proc-ping', proc_path=PROC_PING_PATH, platform='linux/amd64'
    )
    proc_id = meta.obj_id

    if not docker_available:
        yield meta

    else:
        # deploy it
        rti_proxy.deploy(proc_id, session_node.keystore)
        wait_for_processor_ready(rti_proxy, proc_id)
        logger.info(f"Processor deployed: {proc_id}")

        yield meta

        # undeploy it
        rti_proxy.undeploy(proc_id, session_node.keystore)
        wait_for_processor_undeployed(rti_proxy, proc_id)
        logger.info(f"Processor undeployed: {proc_id}")


@pytest.fixture(scope="session")
def deployed_room_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    """Session-scoped fixture that deploys the Room processor for co-simulation tests.

    The Room processor simulates a room's temperature in a co-simulation scenario.

    Yields:
        DataObject representing the deployed processor
    """
    meta = add_test_processor(
        dor_proxy, session_node.keystore, 'proc-room', PROC_ROOM_PATH
    )
    proc_id = meta.obj_id

    if not docker_available:
        yield meta

    else:
        # deploy it
        rti_proxy.deploy(proc_id, session_node.keystore)
        wait_for_processor_ready(rti_proxy, proc_id)
        logger.info(f"Processor deployed: {proc_id}")

        yield meta

        # undeploy it
        rti_proxy.undeploy(proc_id, session_node.keystore)
        wait_for_processor_undeployed(rti_proxy, proc_id)


@pytest.fixture(scope="session")
def deployed_thermostat_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    """Session-scoped fixture that deploys the Thermostat processor for co-simulation tests.

    The Thermostat processor simulates a thermostat controller in a co-simulation scenario.

    Yields:
        DataObject representing the deployed processor
    """
    meta = add_test_processor(
        dor_proxy, session_node.keystore, 'proc-thermostat', PROC_THERMOSTAT_PATH
    )
    proc_id = meta.obj_id

    if not docker_available:
        yield meta

    else:
        # deploy it
        rti_proxy.deploy(proc_id, session_node.keystore)
        wait_for_processor_ready(rti_proxy, proc_id)
        logger.info(f"Processor deployed: {proc_id}")

        yield meta

        # undeploy it
        rti_proxy.undeploy(proc_id, session_node.keystore)
        wait_for_processor_undeployed(rti_proxy, proc_id)


# ==============================================================================
# Docker-Specific Node Fixtures
# ==============================================================================

@pytest.fixture(scope='session')
def docker_non_strict_node(test_context, github_credentials_available):
    """Docker-based node with non-strict deployment mode.

    Non-strict mode allows any user to deploy and undeploy processors.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("docker_non_strict_node", "no-email-provided", path=tempdir, password="password")
        if github_credentials_available:
            keystore.github_credentials.update(
                REPOSITORY_URL,
                GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
            )
        _node = test_context.get_node(keystore, rti_plugin_class=DefaultRTIService, enable_rest=True, strict_deployment=False)
        yield _node


@pytest.fixture(scope='session')
def docker_strict_node(test_context, extra_keystores, github_credentials_available):
    """Docker-based node with strict deployment mode.

    Strict mode requires the node owner to deploy and undeploy processors.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("docker_strict_node", "no-email-provided", path=tempdir, password="password")
        if github_credentials_available:
            keystore.github_credentials.update(
                REPOSITORY_URL,
                GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
            )
        _node = test_context.get_node(keystore, rti_plugin_class=DefaultRTIService, enable_rest=True, strict_deployment=True)
        yield _node


# ==============================================================================
# AWS-Specific Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def ssh_tunnel():
    """Set up an SSH tunnel for AWS connectivity.

    This fixture establishes an SSH tunnel to a bastion host for accessing
    AWS services. It requires SSH_TUNNEL_HOST, SSH_TUNNEL_USER, and
    SSH_TUNNEL_KEY_PATH environment variables to be set.
    """
    ssh_host = os.environ.get("SSH_TUNNEL_HOST")
    ssh_user = os.environ.get("SSH_TUNNEL_USER")
    ssh_key_path = os.environ.get("SSH_TUNNEL_KEY_PATH")

    if not ssh_host or not ssh_user or not ssh_key_path:
        pytest.skip("Skipping test: SSH tunnel credentials are missing.")

    # SSH tunnel command (runs in the background)
    ssh_command = [
        "ssh",
        "-N",  # Do not execute remote commands
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
        with tempfile.TemporaryDirectory() as tempdir:
            rest_address = ('localhost', 5999)
            p2p_address = "tcp://localhost:4999"

            _node = DefaultNode.create(
                keystore=session_keystore, storage_path=tempdir,
                p2p_address=p2p_address, rest_address=rest_address, boot_node_address=rest_address,
                enable_db=True, dor_plugin_class=DefaultDORService, rti_plugin_class=AWSRTIService,
                retain_job_history=True, strict_deployment=False
            )

            yield _node

            _node.shutdown()


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
def aws_non_strict_node(aws_available, test_context, github_credentials_available, session_node):
    """AWS-based node with non-strict deployment mode."""
    if not aws_available:
        yield session_node

    else:
        with tempfile.TemporaryDirectory() as tempdir:
            keystore = Keystore.new("aws_non_strict_node", "no-email-provided", path=tempdir, password="password")
            if github_credentials_available:
                keystore.github_credentials.update(
                    REPOSITORY_URL,
                    GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
                )
            _node = test_context.get_node(keystore, rti_plugin_class=AWSRTIService, enable_rest=True, strict_deployment=False)
            yield _node


@pytest.fixture(scope='session')
def aws_strict_node(aws_available, test_context, extra_keystores, github_credentials_available, session_node):
    """AWS-based node with strict deployment mode."""
    if not aws_available:
        yield session_node

    else:
        with tempfile.TemporaryDirectory() as tempdir:
            keystore = Keystore.new("aws_strict_node", "no-email-provided", path=tempdir, password="password")
            if github_credentials_available:
                keystore.github_credentials.update(
                    REPOSITORY_URL,
                    GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
                )
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
        logger.info(f"AWS Processor deployed: {proc_id}")

        yield meta

        # undeploy it
        aws_rti_proxy.undeploy(proc_id, aws_session_node.keystore)
        wait_for_processor_undeployed(aws_rti_proxy, proc_id)
        logger.info(f"AWS Processor undeployed: {proc_id}")


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
        logger.info(f"AWS Processor deployed: {proc_id}")

        yield meta

        aws_rti_proxy.undeploy(proc_id, aws_session_node.keystore)
        wait_for_processor_undeployed(aws_rti_proxy, proc_id)
        logger.info(f"AWS Processor undeployed: {proc_id}")


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
        logger.info(f"AWS Processor deployed: {proc_id}")

        yield meta

        aws_rti_proxy.undeploy(proc_id, aws_session_node.keystore)
        wait_for_processor_undeployed(aws_rti_proxy, proc_id)
        logger.info(f"AWS Processor undeployed: {proc_id}")


# ==============================================================================
# Known User Fixture
# ==============================================================================

@pytest.fixture()
def known_user(extra_keystores, node_db_proxy):
    """Function-scoped fixture providing a known user for authorization tests.

    Creates a user identity that is registered with the node but is not the
    node owner. Useful for testing authorization restrictions.
    """
    _keystore = extra_keystores[2]
    node_db_proxy.update_identity(_keystore.identity)
    return _keystore


@pytest.fixture()
def aws_known_user(extra_keystores, aws_node_db_proxy):
    """Function-scoped fixture providing a known user for AWS authorization tests."""
    _keystore = extra_keystores[2]
    aws_node_db_proxy.update_identity(_keystore.identity)
    return _keystore
