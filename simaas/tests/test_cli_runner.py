"""Tests for CLI job runner functionality."""

import json
import logging
import os
import tempfile
import time

import pytest

from simaas.plugins.builtins.rti_docker import DockerRTIService
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.helpers import PortMaster, determine_local_ip, find_processors
from simaas.node.default import DefaultNode
from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork
from simaas.rti.schemas import JobStatus, ExitCode, JobResult
from simaas.tests.helper_factories import (
    prepare_plain_job_folder, ProcessorRunner, execute_job, prepare_data_object
)

logger = Logging.get(__name__)
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir



def test_job_worker_done(temp_dir):
    """Test job worker successful completion."""
    job_id = 'abcd1234_00'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 1, 1)

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('proc-abc')
    assert(proc is not None)

    worker = ProcessorRunner(proc, job_path, logging.INFO)
    worker.start()
    worker.join()

    expected_files = ['c', 'job.exitcode', 'job.status', 'job.log']
    for file in expected_files:
        if not os.path.isfile(os.path.join(job_path, file)):
            assert False

    exitcode_path = os.path.join(job_path, 'job.exitcode')
    with open(exitcode_path, 'r') as f:
        result = JobResult.model_validate(json.load(f))

    assert result.exitcode == ExitCode.DONE



def test_job_worker_interrupted(temp_dir):
    """Test job worker interruption handling."""
    job_id = 'abcd1234_01'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 5, 5)

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('proc-abc')
    assert(proc is not None)

    worker = ProcessorRunner(proc, job_path, logging.INFO)
    worker.start()
    worker.interrupt()
    worker.join()

    expected_files = ['job.exitcode', 'job.status', 'job.log']
    for file in expected_files:
        if not os.path.isfile(os.path.join(job_path, file)):
            assert False

    exitcode_path = os.path.join(job_path, 'job.exitcode')
    with open(exitcode_path, 'r') as f:
        result = JobResult.model_validate(json.load(f))

    assert result.exitcode == ExitCode.INTERRUPTED



def test_job_worker_error(temp_dir):
    """Test job worker error handling."""
    job_id = 'abcd1234_02'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 1, 'sdf')

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('proc-abc')
    assert(proc is not None)

    worker = ProcessorRunner(proc, job_path, logging.INFO)
    worker.start()
    worker.join()

    expected_files = ['job.exitcode', 'job.status', 'job.log']
    for file in expected_files:
        if not os.path.isfile(os.path.join(job_path, file)):
            assert False

    exitcode_path = os.path.join(job_path, 'job.exitcode')
    with open(exitcode_path, 'r') as f:
        result = JobResult.model_validate(json.load(f))

    assert result.exitcode == ExitCode.ERROR
    assert "ValueError: invalid literal for int() with base 10: 'sdf'" in result.trace


@pytest.mark.asyncio

async def test_cli_runner_success_by_value(temp_dir, session_node):
    """Test job runner with input values."""
    a: int = 1
    b: int = 1
    job_id = '398h36g3_00'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 100


@pytest.mark.asyncio

async def test_cli_runner_failing_validation(temp_dir, session_node):
    """Test job runner input validation failure."""
    a: int = {'wrong': 55}
    b: int = 1
    job_id = '398h36g3_01'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    assert 'Data object JSON content does not comply' in status.errors[0].exception.reason


@pytest.mark.asyncio

async def test_runner_by_reference(temp_dir, session_node):
    """Test job runner with data object reference inputs."""
    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_02'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 100


@pytest.mark.asyncio

async def test_cli_runner_failing_no_access(temp_dir, session_node, extra_keystores):
    """Test job runner access control enforcement."""
    user = extra_keystores[0]
    await session_node.db.update_identity(user.identity)

    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, access=[session_node.identity])
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1, access=[session_node.identity])
    job_id = '398h36g3_03'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b, user=user.identity)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'AccessNotPermittedError' in trace


@pytest.mark.asyncio

async def test_runner_no_signature(temp_dir, session_node):
    """Test job runner signature requirement enforcement."""
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, access=[session_node.identity])
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1, access=[session_node.identity])
    job_id = '398h36g3_04'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'MissingUserSignatureError' in trace


@pytest.mark.asyncio

async def test_runner_missing_object(temp_dir, session_node):
    """Test job runner missing data object handling."""
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_05'

    # delete the object so it can't be found
    proxy = DORProxy(session_node.rest.address())
    proxy.delete_data_object(b.obj_id, session_node.keystore)

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'UnresolvedInputDataObjectsError' in trace


@pytest.mark.asyncio

async def test_runner_wrong_type(temp_dir, session_node):
    """Test job runner data type validation."""
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, data_type='wrong')
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_06'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'MismatchingDataTypeOrFormatError' in trace


@pytest.mark.asyncio

async def test_runner_wrong_format(temp_dir, session_node):
    """Test job runner data format validation."""
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, data_type='data_format')
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_07'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'MismatchingDataTypeOrFormatError' in trace


@pytest.mark.asyncio
@pytest.mark.skip(reason="Direct P2P interrupt test conflicts with RTI cancel flow optimization - see SPECIFICATION.md")
async def test_cli_runner_cancelled(temp_dir, session_node):
    """Test job runner cancellation handling."""
    a: int = 5
    b: int = 6
    job_id = '398h36g3_08'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b, cancel=True)
    assert len(status.errors) == 0
    assert status.progress < 100
    assert status.state == JobStatus.State.CANCELLED


@pytest.mark.asyncio

async def test_runner_non_dor(temp_dir, session_node):
    """Test job runner target node DOR capability validation."""
    # create a new node as DOR target
    with tempfile.TemporaryDirectory() as target_node_storage_path:
        local_ip = determine_local_ip()
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)
        target_node = DefaultNode.create(
            keystore=Keystore.new('dor-target'), storage_path=target_node_storage_path,
            p2p_address=p2p_address, rest_address=rest_address,
            enable_db=True, dor_plugin_class=None, rti_plugin_class=DockerRTIService,
            retain_job_history=True, strict_deployment=False
        )

        #  make exec-only node known to node
        await P2PJoinNetwork(target_node).perform(session_node.info)
        time.sleep(1)

        a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1)
        b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
        job_id = '398h36g3_09'

        # execute the job
        status = await execute_job(temp_dir, session_node, job_id, a, b, target_node=target_node)
        assert 'Target node does not support DOR capabilities' == status.errors[0].exception.details['reason']

        # leave the network
        protocol = P2PLeaveNetwork(target_node)
        await protocol.perform(blocking=True)

        # shutdown the target node
        target_node.shutdown()

        network = await session_node.db.get_network()
        assert len(network) == 2


@pytest.mark.asyncio

async def test_runner_coupled(temp_dir, session_node):
    """Test job runner with batch coupling."""
    a: int = 1
    b: int = 1
    job_id = '398h36g3_100'
    batch_id = 'batch001'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b, batch_id=batch_id)
    assert status.progress == 100


