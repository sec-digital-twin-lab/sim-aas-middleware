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
from simaas.tests.conftest import BASE_DIR, DummyProgressListener

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture
def tcp_udp_server():
    """Fixture that starts a test server for TCP/UDP connectivity testing"""
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


def test_tcp_connection(tcp_udp_server):
    """Test TCP connection functionality"""
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


def test_udp_connection(tcp_udp_server):
    """Test UDP connection functionality"""
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


def test_proc_ping_only(dummy_namespace):
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


def test_proc_ping_tcp_udp(dummy_namespace, tcp_udp_server):
    """Test processor with TCP and UDP connectivity tests enabled"""
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


def test_ping_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_ping_processor, tcp_udp_server
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_ping_processor.obj_id
    owner = session_node.keystore
    tcp_port = tcp_udp_server['tcp_port']
    udp_port = tcp_udp_server['udp_port']

    # submit the task
    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'parameters', 'type': 'value', 'value': {
                    "address": "192.168.50.117",
                    "do_ping": False,
                    "do_traceroute": False,
                    "do_tcp_test": True,
                    "tcp_port": tcp_port,
                    "tcp_timeout": 5,
                    "do_udp_test": True,
                    "udp_port": udp_port,
                    "udp_timeout": 5
                }
            }),
        ],
        output=[
            Task.Output.model_validate({
                'name': 'result',
                'owner_iid': owner.identity.id,
                'restricted_access': False,
                'content_encrypted': False,
                'target_node_iid': None
            })
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

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
        print(json.dumps(result, indent=4))
