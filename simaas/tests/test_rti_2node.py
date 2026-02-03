"""RTI 2-Node P2P integration tests.

Tests RTI functionality with a 2-node setup where:
- Storage Node: DOR enabled, RTI disabled
- Execution Node: DOR disabled, RTI enabled

This forces all data to flow through P2P, exposing issues single-node tests cannot detect.
"""

import json
import logging
import os
import random
import tempfile
import threading
import time
import traceback
from typing import Optional, List

import pytest

from simaas.core.async_helpers import run_coro_safely
from simaas.core.identity import Identity
from simaas.core.logging import get_logger, initialise
from simaas.core.errors import RemoteError
from simaas.dor.schemas import DataObject
from simaas.helpers import docker_container_list
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import Task, JobStatus, Job, BatchStatus

from examples.cosim.room.processor import Result as RResult
from examples.cosim.thermostat.processor import Result as TResult

from simaas.tests.fixture_rti_2node import RTI2NodeContext
from simaas.tests.helper_waiters import wait_for_job_completion
from simaas.tests.helper_factories import create_abc_task, TaskBuilder
from simaas.tests.helper_assertions import (
    assert_job_successful,
    assert_job_failed,
    assert_job_cancelled,
    assert_data_object_content,
    count_runner_identities,
    assert_runner_identities_cleaned_up,
)

initialise(level=logging.DEBUG)
log = get_logger(__name__, 'test')


# ==============================================================================
# Helper Functions
# ==============================================================================

def get_cosim_tasks_2node(
    deployed_room_processor: DataObject,
    deployed_thermostat_processor: DataObject,
    owner: Identity,
    storage_node_iid: str,
    namespace: Optional[str] = None,
    duplicate_name: Optional[str] = None,
    memory: int = 1024
) -> List[Task]:
    """Create co-simulation tasks with outputs targeting storage node.

    In 2-node setup, outputs must target the storage node since the
    execution node has no DOR.
    """
    return [
        Task(
            proc_id=deployed_room_processor.obj_id,
            user_iid=owner.id,
            input=[
                Task.InputValue.model_validate({
                    'name': 'parameters', 'type': 'value', 'value': {
                        'initial_temp': 20,
                        'heating_rate': 0.5,
                        'cooling_rate': -0.2,
                        'max_steps': 100
                    }
                })
            ],
            output=[
                Task.Output.model_validate({
                    'name': 'result',
                    'owner_iid': owner.id,
                    'restricted_access': False,
                    'content_encrypted': False,
                    'target_node_iid': storage_node_iid
                })
            ],
            name=duplicate_name if duplicate_name else 'room',
            description=None,
            budget=ResourceDescriptor(vcpus=1, memory=memory),
            namespace=namespace
        ),
        Task(
            proc_id=deployed_thermostat_processor.obj_id,
            user_iid=owner.id,
            input=[
                Task.InputValue.model_validate({
                    'name': 'parameters', 'type': 'value', 'value': {
                        'threshold_low': 18.0,
                        'threshold_high': 22.0
                    }
                })
            ],
            output=[
                Task.Output.model_validate({
                    'name': 'result',
                    'owner_iid': owner.id,
                    'restricted_access': False,
                    'content_encrypted': False,
                    'target_node_iid': storage_node_iid
                })
            ],
            name=duplicate_name if duplicate_name else 'thermostat',
            description=None,
            budget=ResourceDescriptor(vcpus=1, memory=memory),
            namespace=namespace
        )
    ]


# ==============================================================================
# Job Submission and Execution Tests (2-Node)
# ==============================================================================

@pytest.mark.integration
def test_job_submit_and_retrieve_2node(rti_2node_context: RTI2NodeContext, test_context, extra_keystores):
    """Test job submission and status retrieval in 2-node setup.

    Data flows:
    - Task submitted to execution node's RTI
    - Output data object stored on storage node via P2P
    - Content retrieved from storage node
    """
    ctx = rti_2node_context
    proc_id = ctx.deployed_abc_processor.obj_id
    wrong_user = ctx.get_known_user(extra_keystores)
    owner = ctx.execution_node.keystore

    # Count runner identities before job submission
    runner_count_before = count_runner_identities(ctx.execution_nodedb_proxy)

    # Create task with output targeting storage node
    task = create_abc_task(
        proc_id, owner, a=1, b=1,
        memory=ctx.default_memory,
        target_node_iid=ctx.storage_node.identity.id
    )

    # Submit the job to execution node
    jobs = ctx.execution_rti_proxy.submit([task], with_authorisation_by=owner)
    job = jobs[0]
    assert job is not None

    job_id = job.id

    # Get list of all jobs by correct user
    result = ctx.execution_rti_proxy.get_jobs_by_user(owner)
    assert result is not None
    result = {job.id: job for job in result}
    assert job_id in result

    # Get list of all jobs by wrong user
    result = ctx.execution_rti_proxy.get_jobs_by_user(wrong_user)
    assert result is not None
    assert len(result) == 0

    # Get list of all jobs by proc
    result = ctx.execution_rti_proxy.get_jobs_by_proc(proc_id)
    assert result is not None
    assert len(result) == 1

    # Try to get the job info as the wrong user
    with pytest.raises(RemoteError) as e:
        ctx.execution_rti_proxy.get_job_status(job_id, wrong_user)
    assert 'authorisation denied' in e.value.reason.lower() or 'job_owner or node_owner' in e.value.reason.lower()

    # Wait for job completion
    status = wait_for_job_completion(ctx.execution_rti_proxy, job_id, owner)

    # Verify job succeeded with expected output
    assert_job_successful(status, expected_outputs=['c'])

    # Verify output was stored on storage node via P2P
    assert_data_object_content(
        ctx.storage_dor_proxy, status.output['c'].obj_id, owner,
        expected={'v': 2}, temp_dir=test_context.testing_dir
    )

    # Verify runner identity was cleaned up after job completion
    assert_runner_identities_cleaned_up(ctx.execution_nodedb_proxy, runner_count_before)


@pytest.mark.integration
@pytest.mark.docker_only
def test_job_cancel_by_owner_2node(docker_available, rti_2node_context: RTI2NodeContext, extra_keystores):
    """Test job cancellation by the job owner in 2-node setup.

    P2P cancel signal crosses from execution node to job runner.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    ctx = rti_2node_context
    proc_id = ctx.deployed_abc_processor.obj_id
    wrong_user = ctx.get_known_user(extra_keystores)
    owner = ctx.execution_node.keystore

    # Count runner identities before job submission
    runner_count_before = count_runner_identities(ctx.execution_nodedb_proxy)

    # Create task with large input values for longer execution
    task = create_abc_task(
        proc_id, owner, a=100, b=100,
        target_node_iid=ctx.storage_node.identity.id
    )

    # Submit the job
    results = ctx.execution_rti_proxy.submit([task], owner)
    assert results is not None

    job_id = results[0].id

    # Try to cancel the job (wrong user)
    with pytest.raises(RemoteError) as e:
        ctx.execution_rti_proxy.cancel_job(job_id, wrong_user)
    assert 'authorisation denied' in e.value.reason.lower() or 'job' in e.value.reason.lower()

    # Wait until the job is running
    while True:
        status: JobStatus = ctx.execution_rti_proxy.get_job_status(job_id, owner)
        if status.state == JobStatus.State.RUNNING:
            break
        time.sleep(0.5)

    # Cancel the job (correct user)
    ctx.execution_rti_proxy.cancel_job(job_id, owner)

    # Wait for cancellation to complete
    status = wait_for_job_completion(ctx.execution_rti_proxy, job_id, owner)
    assert_job_cancelled(status)

    # Verify runner identity was cleaned up after job cancellation
    assert_runner_identities_cleaned_up(ctx.execution_nodedb_proxy, runner_count_before)


@pytest.mark.integration
@pytest.mark.docker_only
@pytest.mark.slow
def test_job_cancel_with_force_kill_2node(docker_available, rti_2node_context: RTI2NodeContext):
    """Test job cancellation with forced container kill in 2-node setup.

    Uses docker_container_list() to verify container state.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    ctx = rti_2node_context
    proc_id = ctx.deployed_abc_processor.obj_id
    owner = ctx.execution_node.keystore

    # a=-100 triggers special behavior in ABC processor that ignores interrupt signals
    task = create_abc_task(
        proc_id, owner, a=-100, b=100,
        target_node_iid=ctx.storage_node.identity.id
    )

    # Submit the job
    result = ctx.execution_rti_proxy.submit([task], owner)
    assert result is not None

    job_id = result[0].id

    # Wait until the job is running
    while True:
        status: JobStatus = ctx.execution_rti_proxy.get_job_status(job_id, owner)
        if status.state == JobStatus.State.RUNNING:
            break
        time.sleep(0.5)

    containers = docker_container_list()
    n0 = len(containers)

    # Cancel the job (correct user)
    ctx.execution_rti_proxy.cancel_job(job_id, owner)

    # Give it a bit...
    time.sleep(5)

    containers = docker_container_list()
    n1 = len(containers)

    # The job should still be running because interrupt doesn't work
    assert n0 == n1

    # Give it a bit more for the grace period to end...
    time.sleep(30)

    containers = docker_container_list()
    n2 = len(containers)

    # The job should be cancelled now because the container was killed
    assert n2 == n1 - 1


@pytest.mark.integration
def test_job_provenance_tracking_2node(rti_2node_context: RTI2NodeContext):
    """Test job provenance tracking through iterative computations in 2-node setup.

    This test focuses on verifying that outputs flow via P2P to the storage node
    and that provenance is correctly tracked. It uses input values (not references)
    to avoid Docker networking issues where job runners inside containers cannot
    reach the host's P2P service to fetch reference inputs.

    The first iteration produces c1 = 1 + 1 = 2
    The second iteration uses c1 as value: c2 = c1 + 1 = 3
    The third iteration uses c2 as value: c3 = c2 + 1 = 4
    """
    from simaas.rti.base import RTIServiceBase

    ctx = rti_2node_context
    rti: RTIServiceBase = ctx.execution_node.rti
    owner = ctx.execution_node.keystore

    def load_value(obj: DataObject) -> int:
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'temp.json')
            ctx.storage_dor_proxy.get_content(obj.obj_id, owner, path)
            with open(path, 'r') as f:
                content = json.load(f)
                value = content['v']
                return value

    # Beginning with input values
    value_a = 1
    value_b = 1

    # Run 3 iterations, using output from previous as value for next
    log_entries = []
    for _ in range(3):
        # Build task with input values and output targeting storage node
        task = create_abc_task(
            ctx.deployed_abc_processor.obj_id, owner,
            a=value_a, b=value_b, memory=ctx.default_memory,
            target_node_iid=ctx.storage_node.identity.id
        )

        # Submit to execution node
        jobs = ctx.execution_rti_proxy.submit([task], owner)
        job = jobs[0]

        # Wait until the job is done
        status: JobStatus = run_coro_safely(rti.get_job_status(job.id))
        while status.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
            status: JobStatus = run_coro_safely(rti.get_job_status(job.id))
            time.sleep(0.5)

        # Verify job succeeded before accessing output
        assert status.state == JobStatus.State.SUCCESSFUL, f"Job failed with state {status.state}, errors: {status.errors}"
        assert 'c' in status.output, f"Output 'c' not found, available outputs: {list(status.output.keys())}"

        obj_c = status.output['c']
        value_c = load_value(obj_c)

        log_entries.append(((value_a, value_b, value_c), obj_c.c_hash))

        # Use output value as input for next iteration
        value_b = value_c

    for item in log_entries:
        print(f"{item[0][0]} + {item[0][1]} = {item[0][2]}\t(hash: {item[1]})")

    # Get the provenance from storage node and print it
    provenance = ctx.storage_dor_proxy.get_provenance(log_entries[2][1])
    assert provenance is not None
    print(json.dumps(provenance.model_dump(), indent=2))


@pytest.mark.integration
@pytest.mark.slow
def test_job_concurrent_execution_2node(rti_2node_context: RTI2NodeContext, test_context, n: int = 20):
    """Test concurrent job execution in 2-node setup.

    Concurrent P2P from execution node to storage node for output storage.
    """
    from simaas.rti.base import RTIServiceBase

    ctx = rti_2node_context
    wd_path = test_context.testing_dir
    owner = ctx.execution_node.keystore
    results = {}
    failed = {}
    logs = {}
    mutex = threading.Lock()
    rnd = random.Random()
    rti: RTIServiceBase = ctx.execution_node.rti

    def logprint(idx: int, m: str) -> None:
        print(m)
        with mutex:
            logs[idx].append(m)

    def do_a_job(idx: int) -> None:
        try:
            with mutex:
                logs[idx] = []

            dt = rnd.randint(0, 1000) / 1000.0
            v0 = rnd.randint(2, 6)
            v1 = rnd.randint(2, 6)

            time.sleep(dt)

            logprint(idx, f"[{idx}] [{time.time()}] submit job")

            task = create_abc_task(
                ctx.deployed_abc_processor.obj_id, owner,
                a=v0, b=v1, memory=ctx.default_memory,
                target_node_iid=ctx.storage_node.identity.id
            )
            jobs = ctx.execution_rti_proxy.submit([task], owner)
            job = jobs[0]

            logprint(idx, f"[{idx}] [{time.time()}] job {job.id} submitted: {os.path.join(rti._jobs_path, job.id)}")

            # Wait until the job is done
            status: JobStatus = run_coro_safely(rti.get_job_status(job.id))
            while status.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                status: JobStatus = run_coro_safely(rti.get_job_status(job.id))
                time.sleep(1.0)

            logprint(idx, f"[{idx}] [{time.time()}] job {job.id} finished: {status.state}")

            if status.state != JobStatus.State.SUCCESSFUL:
                raise RuntimeError(f"[{idx}] failed: {status.state}")

            obj_id = status.output['c'].obj_id
            logprint(idx, f"[{idx}] obj_id: {obj_id}")

            download_path = os.path.join(wd_path, f"{obj_id}.json")
            while True:
                try:
                    logprint(idx, f"[{idx}] do fetch {obj_id} from storage node")
                    ctx.storage_dor_proxy.get_content(obj_id, owner, download_path)
                    logprint(idx, f"[{idx}] fetch returned {obj_id}")
                    break
                except RemoteError as e:
                    logprint(idx, f"[{idx}] error while get content: {e}")
                    time.sleep(0.5)

            with open(download_path, 'r') as f:
                content = json.load(f)
                with mutex:
                    results[idx] = content['v']

            logprint(idx, f"[{idx}] done")

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            with mutex:
                failed[idx] = e
            logprint(idx, f"[{idx}] failed: {trace}")

    # Submit jobs
    threads = []
    for i in range(n):
        thread = threading.Thread(target=do_a_job, kwargs={'idx': i})
        thread.start()
        threads.append(thread)

    # Wait for all the threads
    for thread in threads:
        thread.join(60)

    for i in range(n):
        print(f"### {i} ###")
        job_log = logs[i]
        for msg in job_log:
            print(msg)
        print("###")

    for i, e in failed.items():
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        print(f"[{i}] failed: {trace}")
    assert len(failed) == 0
    assert len(results) == n


# ==============================================================================
# Batch Job Tests (2-Node)
# ==============================================================================

@pytest.mark.integration
def test_batch_submit_and_complete_2node(rti_2node_context: RTI2NodeContext, test_context, extra_keystores, n: int = 5):
    """Test batch job submission and completion in 2-node setup."""
    ctx = rti_2node_context
    proc_id = ctx.deployed_abc_processor.obj_id
    wrong_user = ctx.get_known_user(extra_keystores)
    owner = ctx.execution_node.keystore

    # Create batch tasks using TaskBuilder with storage target
    tasks = [
        (TaskBuilder(proc_id, owner.identity.id)
         .with_name(f'task_{i}')
         .with_input_value('a', {'v': 1})
         .with_input_value('b', {'v': 5})
         .with_output('c', owner.identity.id, target_node_iid=ctx.storage_node.identity.id)
         .with_budget(memory=ctx.default_memory)
         .build())
        for i in range(n)
    ]

    # Submit the batch to execution node
    jobs = ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert len(jobs) == len(tasks)

    # Determine batch id
    batch_id = jobs[0].batch_id

    # Try to get the batch info as the wrong user
    with pytest.raises(RemoteError) as e:
        ctx.execution_rti_proxy.get_batch_status(batch_id, wrong_user)
    assert 'authorisation denied' in e.value.reason.lower() or 'batch' in e.value.reason.lower()

    # Wait for all batch members to complete
    while True:
        status: BatchStatus = ctx.execution_rti_proxy.get_batch_status(batch_id, owner)
        all_finished = all(
            member.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]
            for member in status.members
        )
        if all_finished:
            break
        time.sleep(1)

    # Verify all batch members completed with expected output
    for member in status.members:
        job_status: JobStatus = ctx.execution_rti_proxy.get_job_status(member.job_id, owner)
        assert_job_successful(job_status, expected_outputs=['c'])
        # Verify output was stored on storage node
        assert_data_object_content(
            ctx.storage_dor_proxy, job_status.output['c'].obj_id, owner,
            expected={'v': 6}, temp_dir=test_context.testing_dir
        )


@pytest.mark.integration
def test_batch_cancel_cascade_2node(rti_2node_context: RTI2NodeContext):
    """Test batch cancellation cascade in 2-node setup.

    Cascade via P2P from execution node to job runners.
    """
    ctx = rti_2node_context
    proc_id = ctx.deployed_abc_processor.obj_id
    owner = ctx.execution_node.keystore

    # Create batch tasks using TaskBuilder with storage target
    tasks = [
        (TaskBuilder(proc_id, owner.identity.id)
         .with_name(f'task_{i}')
         .with_input_value('a', {'v': 30})
         .with_input_value('b', {'v': 30})
         .with_output('c', owner.identity.id, target_node_iid=ctx.storage_node.identity.id)
         .with_budget(memory=ctx.default_memory)
         .build())
        for i in range(10)
    ]

    # Modify the first task to trigger a failure (invalid values cause exception)
    # This should trigger the cancellation cascade.
    tasks[0].input[0].value = {'v': 5}
    tasks[0].input[1].value = {'v': 1000}

    # Submit the batch to execution node
    jobs = ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert len(jobs) == len(tasks)

    # Determine batch id
    batch_id = jobs[0].batch_id

    # Wait for all batch members to complete
    while True:
        status: BatchStatus = ctx.execution_rti_proxy.get_batch_status(batch_id, owner)
        all_finished = all(
            member.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]
            for member in status.members
        )
        if all_finished:
            break
        time.sleep(1)

    # Verify first job failed and others were cancelled
    for i, member in enumerate(status.members):
        job_status: JobStatus = ctx.execution_rti_proxy.get_job_status(member.job_id, owner)
        if i == 0:
            assert_job_failed(job_status)
        else:
            assert_job_cancelled(job_status)


# ==============================================================================
# Co-Simulation Tests (2-Node)
# ==============================================================================

@pytest.mark.integration
def test_cosim_room_thermostat_2node(rti_2node_context: RTI2NodeContext, test_context):
    """Test co-simulation with room and thermostat processors in 2-node setup."""
    ctx = rti_2node_context
    owner = ctx.execution_node.keystore

    # Get the co-sim tasks with outputs targeting storage node
    tasks = get_cosim_tasks_2node(
        ctx.deployed_room_processor,
        ctx.deployed_thermostat_processor,
        owner.identity,
        ctx.storage_node.identity.id,
        memory=ctx.default_memory
    )

    # Submit the job to execution node
    result = ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
    jobs = result

    batch_id = jobs[0].batch_id

    while True:
        try:
            status: BatchStatus = ctx.execution_rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert status is not None

            is_done = True
            for member in status.members:
                if member.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                    is_done = False

            if is_done:
                break

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            print(trace)

        time.sleep(1)

    # Get the result of the 'room' and the 'thermostat'
    job_status0: JobStatus = ctx.execution_rti_proxy.get_job_status(jobs[0].id, with_authorisation_by=owner)
    job_status1: JobStatus = ctx.execution_rti_proxy.get_job_status(jobs[1].id, with_authorisation_by=owner)
    assert 'result' in job_status0.output
    assert 'result' in job_status1.output

    # Get the contents from storage node
    download_path0 = os.path.join(test_context.testing_dir, 'result0_2node.json')
    ctx.storage_dor_proxy.get_content(job_status0.output['result'].obj_id, owner, download_path0)
    assert os.path.isfile(download_path0)

    download_path1 = os.path.join(test_context.testing_dir, 'result1_2node.json')
    ctx.storage_dor_proxy.get_content(job_status1.output['result'].obj_id, owner, download_path1)
    assert os.path.isfile(download_path1)

    # Read the results
    with open(download_path0, 'r') as f:
        result0: dict = json.load(f)
        result0: RResult = RResult.model_validate(result0)

    with open(download_path1, 'r') as f:
        result1: dict = json.load(f)
        result1: TResult = TResult.model_validate(result1)

    # Print the result
    print(result0.temp)
    print(result1.state)


# ==============================================================================
# Namespace Tests (2-Node)
# ==============================================================================

@pytest.mark.integration
def test_namespace_resource_limits_2node(rti_2node_context: RTI2NodeContext):
    """Test namespace resource limit enforcement in 2-node setup."""
    ctx = rti_2node_context
    owner = ctx.execution_node.keystore
    mem = ctx.default_memory

    # Test with namespace that has not enough resources for a single task
    namespace0 = 'namespace0_2node'
    run_coro_safely(ctx.execution_node.db.update_namespace_budget(namespace0, ResourceDescriptor(vcpus=1, memory=mem // 2)))

    # Get the tasks for namespace0 and try to submit jobs to namespace0 -> should fail
    tasks = get_cosim_tasks_2node(
        ctx.deployed_room_processor,
        ctx.deployed_thermostat_processor,
        owner.identity,
        ctx.storage_node.identity.id,
        namespace0,
        memory=mem
    )
    with pytest.raises(RemoteError) as e:
        ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
    # Check reason or details for namespace capacity error
    assert ("exceeds" in e.value.reason.lower() or
            "task.budget" in e.value.reason.lower() or
            (e.value.details and "exceeds" in str(e.value.details).lower()))

    # Create namespaces with different resource budgets
    namespace1 = 'namespace1_2node'
    namespace2 = 'namespace2_2node'
    namespace3 = 'namespace3_2node'
    run_coro_safely(ctx.execution_node.db.update_namespace_budget(namespace1, ResourceDescriptor(vcpus=1, memory=mem)))
    run_coro_safely(ctx.execution_node.db.update_namespace_budget(namespace2, ResourceDescriptor(vcpus=2, memory=mem)))
    run_coro_safely(ctx.execution_node.db.update_namespace_budget(namespace3, ResourceDescriptor(vcpus=2, memory=mem * 2)))

    # Get the tasks for namespace1 and try to submit jobs to namespace1 -> should fail
    tasks = get_cosim_tasks_2node(
        ctx.deployed_room_processor,
        ctx.deployed_thermostat_processor,
        owner.identity,
        ctx.storage_node.identity.id,
        namespace1,
        memory=mem
    )
    with pytest.raises(RemoteError) as e:
        ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
    # Check reason or details for combined resource budget error
    assert ("exceeds" in e.value.reason.lower() or
            "combined_budget" in e.value.reason.lower() or
            (e.value.details and "namespace1" in str(e.value.details).lower()))

    # Get the tasks for namespace2 and try to submit jobs to namespace2 -> should fail
    tasks = get_cosim_tasks_2node(
        ctx.deployed_room_processor,
        ctx.deployed_thermostat_processor,
        owner.identity,
        ctx.storage_node.identity.id,
        namespace2,
        memory=mem
    )
    with pytest.raises(RemoteError) as e:
        ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
    # Check reason or details for combined resource budget error
    assert ("exceeds" in e.value.reason.lower() or
            "combined_budget" in e.value.reason.lower() or
            (e.value.details and "namespace2" in str(e.value.details).lower()))

    # Get the tasks for namespace3 and try to submit jobs to namespace3 -> should succeed
    try:
        tasks = get_cosim_tasks_2node(
            ctx.deployed_room_processor,
            ctx.deployed_thermostat_processor,
            owner.identity,
            ctx.storage_node.identity.id,
            namespace3,
            memory=mem
        )
        jobs: List[Job] = ctx.execution_rti_proxy.submit(tasks, with_authorisation_by=owner)
        batch_id = jobs[0].batch_id

        while True:
            time.sleep(1)
            status: BatchStatus = ctx.execution_rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)
            is_done = True
            for member in status.members:
                if member.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                    is_done = False

            if is_done:
                break

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        print(trace)
        assert False
