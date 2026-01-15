"""Pytest configuration and shared fixtures.

This module provides:
- Custom pytest markers configuration
- Re-exports of fixtures from modular fixture files
- RTI-related fixtures (rti_proxy, deployed processors)
- Helper functions for processor testing
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from simaas.cli.cmd_image import build_processor_image
from simaas.core.helpers import get_timestamp_now
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.dor.schemas import ProcessorDescriptor, GitProcessorPointer, DataObject
from simaas.helpers import docker_export_image
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Processor, ProcessorVolume

# Import plugin classes
from plugins.dor_default import DefaultDORService
from plugins.rti_docker import DefaultRTIService

load_dotenv()

# Constants for processor paths
PROC_ABC_PATH = "examples/simple/abc"
PROC_PING_PATH = "examples/simple/ping"

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Deactivate annoying DEBUG messages by multipart
logging.getLogger('multipart.multipart').setLevel(logging.WARNING)
logging.getLogger('python_multipart.multipart').setLevel(logging.WARNING)

logger = Logging.get('tests.conftest')

# ==============================================================================
# Import fixtures from modular fixture files
# These imports make fixtures available to pytest for discovery
# ==============================================================================

# Core fixtures: TestContext, environment checks, keystores
from simaas.tests.fixtures.core import (
    TestContext,
    generate_random_file,
    test_context,
    docker_available,
    aws_available,
    github_credentials_available,
    session_keystore,
    temp_directory,
    extra_keystores,
    REPOSITORY_URL,
    REPOSITORY_COMMIT_ID,
)

# Mock classes for testing
from simaas.tests.fixtures.mocks import (
    DummyProgressListener,
    DummyNamespace,
    dummy_namespace,
)

# DOR fixtures: session_node, proxies
from simaas.tests.fixtures.dor import (
    session_node,
    session_data_dir,
    dor_proxy,
    node_db_proxy,
)


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "serial: mark test to run serially (not in parallel)")
    config.addinivalue_line("markers", "docker_only: mark test to run only with Docker backend")
    config.addinivalue_line("markers", "aws_only: mark test to run only with AWS backend")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark as integration test")
    config.addinivalue_line("markers", "e2e: mark as end-to-end test")


# ==============================================================================
# RTI-related fixtures (to be moved to fixtures/rti.py in Phase 4)
# ==============================================================================

@pytest.fixture(scope="session")
def rti_proxy(session_node):
    """Session-scoped RTI proxy connected to session_node."""
    proxy = RTIProxy(session_node.rest.address())
    return proxy


def add_test_processor(
        dor: DORProxy, keystore: Keystore, proc_name: str, proc_path: str, platform: str = 'linux/amd64'
) -> DataObject:
    """Build and add a processor image to DOR if it doesn't exist."""
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


@pytest.fixture(scope="session")
def deployed_abc_processor(docker_available, rti_proxy, dor_proxy, session_node, session_data_dir) -> DataObject:
    """Session-scoped fixture that deploys the ABC processor."""
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
    """Session-scoped fixture that deploys the Ping processor."""
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
