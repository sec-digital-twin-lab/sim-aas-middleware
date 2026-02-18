"""Pytest configuration and shared fixtures."""

import logging
from pathlib import Path

import pytest  # noqa: F401 - used by pytest
from dotenv import load_dotenv

from simaas.core.logging import get_logger, initialise

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Deactivate annoying DEBUG messages by multipart
logging.getLogger('multipart.multipart').setLevel(logging.WARNING)
logging.getLogger('python_multipart.multipart').setLevel(logging.WARNING)

log = get_logger('tests.conftest', 'test')

# Core fixtures: TestContext, environment checks, keystores
# noqa: F401, E402 - fixtures must be imported here for pytest discovery
from simaas.tests.fixture_core import (  # noqa: E402, F401
    TestContext,
    generate_random_file,
    test_context,
    docker_available,
    aws_available,
    session_keystore,
    temp_directory,
    extra_keystores,
    REPOSITORY_URL,
    CURRENT_COMMIT_ID,
)

# Mock classes for testing
from simaas.tests.fixture_mocks import (  # noqa: E402, F401
    DummyProgressListener,
    DummyNamespace,
    dummy_namespace,
)

# DOR fixtures: session_node, proxies
from simaas.tests.fixture_dor import (  # noqa: E402, F401
    session_node,
    session_data_dir,
    dor_proxy,
    node_db_proxy,
)

# RTI fixtures: rti_proxy, deployed processors, backend fixtures
from simaas.tests.fixture_rti import (  # noqa: E402, F401
    RTIBackend,
    RTIBackendConfig,
    RTIContext,
    ProcessorDeployment,
    check_docker_image_exists,
    add_test_processor,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
    rti_proxy,
    rti_context,
    deployed_abc_processor,
    deployed_ping_processor,
    deployed_room_processor,
    deployed_thermostat_processor,
    deployed_factorisation_processor,
    deployed_factor_search_processor,
    docker_non_strict_node,
    docker_strict_node,
    ssh_tunnel,
    aws_session_node,
    aws_dor_proxy,
    aws_rti_proxy,
    aws_node_db_proxy,
    aws_non_strict_node,
    aws_strict_node,
    aws_deployed_abc_processor,
    aws_deployed_room_processor,
    aws_deployed_thermostat_processor,
    known_user,
    aws_known_user,
    PROC_ABC_PATH,
    PROC_PING_PATH,
    PROC_ROOM_PATH,
    PROC_THERMOSTAT_PATH,
    PROC_FACTORISATION_PATH,
    PROC_FACTOR_SEARCH_PATH,
)

# RTI 2-Node fixtures: separated storage and execution nodes for P2P testing
from simaas.tests.fixture_rti_2node import (  # noqa: E402, F401
    RTI2NodeContext,
    ProcessorDeployment2Node,
    session_keystore_storage,
    session_keystore_execution,
    storage_node,
    execution_node,
    storage_dor_proxy,
    storage_nodedb_proxy,
    execution_rti_proxy,
    execution_nodedb_proxy,
    session_data_dir_2node,
    deployed_abc_processor_2node,
    deployed_room_processor_2node,
    deployed_thermostat_processor_2node,
    rti_2node_context,
)


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "serial: mark test to run serially (not in parallel)")
    config.addinivalue_line("markers", "docker_only: mark test to run only with Docker backend")
    config.addinivalue_line("markers", "aws_only: mark test to run only with AWS backend")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark as integration test")
    config.addinivalue_line("markers", "e2e: mark as end-to-end test")
