"""Pytest configuration and shared fixtures.

This module provides:
- Custom pytest markers configuration
- Re-exports of fixtures from modular fixture files
- RTI-related fixtures imported from fixtures/rti.py
"""

import logging
from pathlib import Path

import pytest
from dotenv import load_dotenv

from simaas.core.logging import Logging

load_dotenv()

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

# RTI fixtures: rti_proxy, deployed processors, backend fixtures
from simaas.tests.fixtures.rti import (
    RTIBackend,
    RTIBackendConfig,
    add_test_processor,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
    rti_proxy,
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


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "serial: mark test to run serially (not in parallel)")
    config.addinivalue_line("markers", "docker_only: mark test to run only with Docker backend")
    config.addinivalue_line("markers", "aws_only: mark test to run only with AWS backend")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark as integration test")
    config.addinivalue_line("markers", "e2e: mark as end-to-end test")
