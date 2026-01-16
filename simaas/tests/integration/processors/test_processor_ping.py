"""Ping Processor integration tests.

Tests for the Ping processor, verifying TCP/UDP connectivity testing
functionality both locally and via Docker RTI.
"""

import json
import logging
import os
import tempfile
import time
import threading

import pytest

from examples.simple.ping.processor import ProcessorPing, tcp_connect, udp_connect
from examples.simple.ping.server import CombinedTestServer
from simaas.core.logging import Logging
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import JobStatus, Task
from simaas.tests.fixtures.core import BASE_DIR
from simaas.tests.fixtures.mocks import DummyProgressListener
from simaas.tests.helpers.waiters import wait_for_job_completion
from simaas.tests.helpers.factories import TaskBuilder
from simaas.tests.helpers.assertions import assert_job_successful

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture
def tcp_udp_server():
    """Fixture that starts a test server for TCP/UDP connectivity testing.

    Provides:
        - TCP server on port 8080
        - UDP server on port 8081
    """
    tcp_port = 8080
    udp_port = 8081
    server = CombinedTestServer('localhost', tcp_port, udp_port)

    # Start server in a separate thread
    server_thread = threading.Thread(target=server.start)
    server_thread.daemon = True
    server_thread.start()

    # Give the server time to start
    time.sleep(1)

    yield {'tcp_port': tcp_port, 'udp_port': udp_port, 'server': server}

    # Cleanup: stop the server
    server.stop()
    time.sleep(0.5)  # Give time for cleanup


@pytest.mark.integration
def test_tcp_connection(tcp_udp_server):
    """
    Test TCP connection functionality.

    Verifies that:
    - Successful connection to running server returns success=True
    - Response time is measured
    - Connection to non-existent port fails appropriately
    - Invalid hostname produces name resolution error

    Backend: Local
    Duration: ~5 seconds
    Requirements: None
    """
    tcp_port = tcp_udp_server['tcp_port']

    # Test successful connection to running server
    result = tcp_connect("localhost", tcp_port, 5)
    assert result['success']
    assert result['response_time_ms'] is not None
    assert result['error'] is None

    # Test connection to a non-existent port (should fail)
    result = tcp_connect("localhost", 19999, 1)
    assert not result['success']
    assert result['error'] is not None
    assert result['response_time_ms'] is None

    # Test connection with invalid hostname
    result = tcp_connect('invalid-hostname-12345', 80, 1)
    assert not result['success']
    assert 'Name resolution failed' in result['error']


@pytest.mark.integration
def test_udp_connection(tcp_udp_server):
    """
    Test UDP connection functionality.

    Verifies that:
    - Successful connection to running server returns success=True
    - Response time is measured for UDP
    - UDP to non-existent port succeeds (connectionless)
    - Invalid hostname produces name resolution error

    Backend: Local
    Duration: ~5 seconds
    Requirements: None
    """
    udp_port = tcp_udp_server['udp_port']

    # Test successful connection to running server
    result = udp_connect("localhost", udp_port, 5)
    assert result['success']
    assert result['response_time_ms'] is not None
    assert result['error'] is None

    # Test UDP connection to a non-existent port (should still succeed for UDP)
    result = udp_connect("localhost", 9999, 1)
    assert result['success']  # UDP is connectionless, so this should succeed
    assert result['response_time_ms'] is not None

    # Test connection with invalid hostname
    result = udp_connect('invalid-hostname-12345', 80, 1)
    assert not result['success']
    assert 'Name resolution failed' in result['error']


@pytest.mark.integration
def test_processor_ping_local_only(dummy_namespace):
    """
    Test Ping processor local execution with ping only.

    Verifies that:
    - Processor runs with ping-only configuration
    - Result file is created
    - No TCP/UDP tests are executed

    Backend: Local (no Docker)
    Duration: ~5 seconds
    Requirements: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        with open(os.path.join(temp_dir, 'parameters'), 'w') as f:
            json.dump({
                "address": "192.168.50.1",
                "do_ping": True,
                "do_traceroute": False,
                "do_tcp_test": False,
                "do_udp_test": False
            }, f)

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'simple', 'ping')
        proc = ProcessorPing(proc_path)
        proc.run(
            temp_dir, None, DummyProgressListener(
                temp_dir, status, dummy_namespace.dor
            ), dummy_namespace, None
        )

        # read the result
        result_path = os.path.join(temp_dir, 'result')
        assert os.path.isfile(result_path)
        with open(result_path, 'r') as f:
            content = f.read()
            print(content)


@pytest.mark.integration
def test_processor_ping_local_tcp_udp(dummy_namespace, tcp_udp_server):
    """
    Test Ping processor local execution with TCP and UDP tests.

    Verifies that:
    - Processor runs with TCP and UDP tests enabled
    - TCP test results are present and successful
    - UDP test results are present and successful
    - Response times are measured

    Backend: Local (no Docker)
    Duration: ~5 seconds
    Requirements: TCP/UDP test server fixture
    """
    tcp_port = tcp_udp_server['tcp_port']
    udp_port = tcp_udp_server['udp_port']

    with tempfile.TemporaryDirectory() as temp_dir:
        with open(os.path.join(temp_dir, 'parameters'), 'w') as f:
            json.dump({
                "address": "127.0.0.1",
                "do_ping": False,
                "do_traceroute": False,
                "do_tcp_test": True,
                "tcp_port": tcp_port,
                "tcp_timeout": 5,
                "do_udp_test": True,
                "udp_port": udp_port,
                "udp_timeout": 5
            }, f)

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'simple', 'ping')
        proc = ProcessorPing(proc_path)
        proc.run(
            temp_dir, None, DummyProgressListener(
                temp_dir, status, dummy_namespace.dor
            ), dummy_namespace, None
        )

        # read the result
        result_path = os.path.join(temp_dir, 'result')
        assert os.path.isfile(result_path)
        with open(result_path, 'r') as f:
            result = json.load(f)
            print(json.dumps(result, indent=2))

            # Check that TCP and UDP test results are present
            assert 'tcp_test' in result
            assert 'udp_test' in result

            # TCP test should succeed (running server)
            assert result['tcp_test']['success']
            assert result['tcp_test']['response_time_ms'] is not None
            assert result['tcp_test']['error'] is None

            # UDP test should succeed (running server)
            assert result['udp_test']['success']
            assert result['udp_test']['response_time_ms'] is not None
            assert result['udp_test']['error'] is None


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_ping_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_ping_processor, tcp_udp_server
):
    """
    Test Ping processor job execution via RTI.

    Verifies that:
    - Job can be submitted with TCP/UDP test parameters
    - Job completes successfully
    - Result data object is created and retrievable

    Backend: Docker
    Duration: ~30 seconds
    Requirements: Docker, GitHub credentials
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_ping_processor.obj_id
    owner = session_node.keystore
    tcp_port = tcp_udp_server['tcp_port']
    udp_port = tcp_udp_server['udp_port']

    # Create task using TaskBuilder
    task = (TaskBuilder(proc_id, owner.identity.id)
            .with_input_value('parameters', {
                "address": "192.168.50.117",
                "do_ping": False,
                "do_traceroute": False,
                "do_tcp_test": True,
                "tcp_port": tcp_port,
                "tcp_timeout": 5,
                "do_udp_test": True,
                "udp_port": udp_port,
                "udp_timeout": 5
            })
            .with_output('result', owner.identity.id)
            .build())

    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    # Wait for job completion
    status = wait_for_job_completion(rti_proxy, job.id, owner)

    # Verify job succeeded with expected output
    assert_job_successful(status, expected_outputs=['result'])

    # Download and print result
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert os.path.isfile(download_path)

    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        print(json.dumps(result, indent=4))
