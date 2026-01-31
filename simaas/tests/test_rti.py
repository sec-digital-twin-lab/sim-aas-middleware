"""RTI (Runtime Infrastructure) service integration tests."""

import asyncio
import json
import logging
import os
import random
import tempfile
import threading
import time
import traceback
from typing import Union, Optional, List

import pytest

from simaas.core.identity import Identity
from simaas.core.helpers import generate_random_string
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.dor.schemas import DataObject
from simaas.helpers import docker_container_list
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import NodeInfo, ResourceDescriptor
from simaas.rest.exceptions import UnsuccessfulRequestError
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Task, JobStatus, Job, BatchStatus, ProcessorVolume

from examples.cosim.room.processor import Result as RResult
from examples.cosim.thermostat.processor import Result as TResult

from simaas.tests.fixture_rti import add_test_processor, RTIContext
from simaas.tests.helper_waiters import (
    wait_for_job_completion,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
)
from simaas.tests.helper_factories import create_abc_task, TaskBuilder
from simaas.tests.helper_assertions import (
    assert_job_successful,
    assert_job_failed,
    assert_job_cancelled,
    assert_data_object_content,
)

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


# ==============================================================================
# Helper Functions
# ==============================================================================

def execute_job(proc_id: str, owner: Keystore, rti_proxy: RTIProxy, target_node: NodeInfo,
                a: Union[int, DataObject] = None, b: Union[int, DataObject] = None,
                memory: int = 1024) -> Job:
    """Execute a single job with the ABC processor."""
    if a is None:
        a = 1

    if b is None:
        b = 1

    a = Task.InputReference(name='a', type='reference', obj_id=a.obj_id, user_signature=None, c_hash=None) \
        if isinstance(a, DataObject) else Task.InputValue(name='a', type='value', value={'v': a})

    b = Task.InputReference(name='b', type='reference', obj_id=b.obj_id, user_signature=None, c_hash=None) \
        if isinstance(b, DataObject) else Task.InputValue(name='b', type='value', value={'v': b})

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[a, b],
        output=[
            Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                        'restricted_access': False, 'content_encrypted': False,
                                        'target_node_iid': target_node.identity.id})
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=memory),
        namespace=None
    )

    # submit the job
    result = rti_proxy.submit([task], owner)
    return result[0]


def get_cosim_tasks(
        deployed_room_processor, deployed_thermostat_processor, owner: Identity, namespace: Optional[str] = None,
        duplicate_name: Optional[str] = None, memory: int = 1024
) -> List[Task]:
    """Create co-simulation tasks for room and thermostat processors."""
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
                    'target_node_iid': None
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
                    'target_node_iid': None
                })
            ],
            name=duplicate_name if duplicate_name else 'thermostat',
            description=None,
            budget=ResourceDescriptor(vcpus=1, memory=memory),
            namespace=namespace
        )
    ]


# ==============================================================================
# Processor Management Tests
# ==============================================================================

@pytest.mark.integration
def test_processor_get_all(rti_context: RTIContext):
    """Test retrieving all deployed processors."""
    result = rti_context.rti_proxy.get_all_procs()
    assert result is not None


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_deploy_and_undeploy(docker_available, docker_non_strict_node, docker_strict_node, known_user):
    """Test processor deployment and undeployment with strict/non-strict modes.

    NOTE: This test is Docker-only because it requires two separate nodes with
    different strict deployment modes (strict vs non-strict). This multi-node
    configuration is specific to the Docker testing environment.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    node0 = docker_non_strict_node
    db0 = NodeDBProxy(node0.rest.address())
    dor0 = DORProxy(node0.rest.address())
    rti0 = RTIProxy(node0.rest.address())

    node1 = docker_strict_node
    db1 = NodeDBProxy(node1.rest.address())
    dor1 = DORProxy(node1.rest.address())
    rti1 = RTIProxy(node1.rest.address())

    # check flags
    info0 = db0.get_node()
    info1 = db1.get_node()
    assert info0.strict_deployment is False
    assert info1.strict_deployment is True

    # upload the test proc GCC
    proc0: DataObject = add_test_processor(dor0, node0.keystore, 'proc-abc', 'examples/simple/abc')
    proc_id0 = proc0.obj_id
    proc1: DataObject = add_test_processor(dor1, node1.keystore, 'proc-abc', 'examples/simple/abc')
    proc_id1 = proc1.obj_id

    # make the wrong user identity known to the nodes
    wrong_user = known_user
    db0.update_identity(wrong_user.identity)
    db1.update_identity(wrong_user.identity)

    # try to deploy the processor with the wrong user on node0 (should succeed - non-strict)
    rti0.deploy(proc_id0, wrong_user)
    wait_for_processor_ready(rti0, proc_id0)

    # try to deploy the processor with the wrong user on node1 (should fail - strict)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti1.deploy(proc_id1, wrong_user)
    assert 'User is not the node owner' in e.value.details['reason']

    # try to deploy the processor with the correct user on node1
    rti1.deploy(proc_id1, node1.keystore)
    wait_for_processor_ready(rti1, proc_id1)

    # try to undeploy the processor with the wrong user on node0 (should succeed - non-strict)
    rti0.undeploy(proc_id0, wrong_user)
    wait_for_processor_undeployed(rti0, proc_id0)

    # try to undeploy the processor with the wrong user on node1 (should fail - strict)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti1.undeploy(proc_id1, wrong_user)
    assert 'User is not the node owner' in e.value.details['reason']

    # try to undeploy the processor with the correct user on node1
    rti1.undeploy(proc_id1, node1.keystore)
    wait_for_processor_undeployed(rti1, proc_id1)


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_deploy_with_volume(docker_available, docker_non_strict_node):
    """Test processor deployment with a mounted volume.

    NOTE: This test is Docker-only because it tests local volume mounting,
    which is a Docker-specific feature. AWS uses EFS volumes with different
    configuration and semantics.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    user: Keystore = docker_non_strict_node.keystore
    dor = DORProxy(docker_non_strict_node.rest.address())
    rti = RTIProxy(docker_non_strict_node.rest.address())

    # upload the test proc GCC
    proc: DataObject = add_test_processor(dor, user, 'proc-abc', 'examples/simple/abc')
    proc_id = proc.obj_id

    with tempfile.TemporaryDirectory() as temp_dir:
        rti.deploy(proc_id, user, volumes=[
            ProcessorVolume(name='data_volume', mount_point='/data', read_only=False, reference={
                'path': temp_dir,
            })
        ])

        # wait for deployment to be done
        proc_status = wait_for_processor_ready(rti, proc_id)
        print(proc_status.volumes)
        assert proc_status.volumes[0].name == 'data_volume'

        # undeploy the processor
        rti.undeploy(proc_id, user)


# ==============================================================================
# Job Submission and Execution Tests
# ==============================================================================

@pytest.mark.integration
def test_job_submit_and_retrieve(rti_context: RTIContext, test_context, extra_keystores):
    """Test job submission and status retrieval."""
    proc_id = rti_context.deployed_abc_processor.obj_id
    wrong_user = rti_context.get_known_user(extra_keystores)
    owner = rti_context.session_node.keystore

    # Create task using factory
    task = create_abc_task(proc_id, owner, a=1, b=1, memory=rti_context.default_memory)

    # submit the job
    jobs = rti_context.rti_proxy.submit([task], with_authorisation_by=owner)
    job = jobs[0]
    assert job is not None

    job_id = job.id

    # get list of all jobs by correct user
    result = rti_context.rti_proxy.get_jobs_by_user(owner)
    assert result is not None
    result = {job.id: job for job in result}
    assert job_id in result

    # get list of all jobs by wrong user
    result = rti_context.rti_proxy.get_jobs_by_user(wrong_user)
    assert result is not None
    assert len(result) == 0

    # get list of all jobs by proc
    result = rti_context.rti_proxy.get_jobs_by_proc(proc_id)
    assert result is not None
    assert len(result) == 1

    # try to get the job info as the wrong user
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_context.rti_proxy.get_job_status(job_id, wrong_user)
    assert e.value.details['reason'] == 'user is not the job owner or the node owner'

    # Wait for job completion
    status = wait_for_job_completion(rti_context.rti_proxy, job_id, owner)

    # Verify job succeeded with expected output
    assert_job_successful(status, expected_outputs=['c'])
    assert_data_object_content(
        rti_context.dor_proxy, status.output['c'].obj_id, owner,
        expected={'v': 2}, temp_dir=test_context.testing_dir
    )


@pytest.mark.integration
@pytest.mark.docker_only
def test_job_cancel_by_owner(docker_available, session_node, rti_proxy, deployed_abc_processor, known_user):
    """Test job cancellation by the job owner.

    NOTE: This test is Docker-only due to network limitations when testing AWS.
    While AWS jobs can reach the local custodian node (via SSH tunnel), the local
    node cannot reach jobs running on AWS's internal network to send P2P cancel
    signals. In production where the custodian runs on AWS infrastructure,
    cancellation works normally.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    wrong_user = known_user
    owner = session_node.keystore

    # Create task with large input values for longer execution
    task = create_abc_task(proc_id, owner, a=100, b=100)

    # submit the job
    results = rti_proxy.submit([task], owner)
    assert results is not None

    job_id = results[0].id

    # try to cancel the job (wrong user)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_proxy.cancel_job(job_id, wrong_user)
    assert 'user is not the job owner' in e.value.details['reason']

    # wait until the job is running
    while True:
        status: JobStatus = rti_proxy.get_job_status(job_id, owner)
        if status.state == JobStatus.State.RUNNING:
            break
        time.sleep(0.5)

    # cancel the job (correct user)
    rti_proxy.cancel_job(job_id, owner)

    # Wait for cancellation to complete
    status = wait_for_job_completion(rti_proxy, job_id, owner)
    assert_job_cancelled(status)


@pytest.mark.integration
@pytest.mark.docker_only
@pytest.mark.slow
def test_job_cancel_with_force_kill(docker_available, session_node, rti_proxy, deployed_abc_processor, known_user):
    """Test job cancellation with forced container kill after grace period.

    NOTE: This test is Docker-only for two reasons:
    1. It uses docker_container_list() to inspect container state, which is
       Docker-specific functionality.
    2. SSH tunnel limitations prevent cancellation testing with AWS (see
       test_job_cancel_by_owner for details).
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    # a=-100 triggers special behavior in ABC processor that ignores interrupt signals
    task = create_abc_task(proc_id, owner, a=-100, b=100)

    # submit the job
    result = rti_proxy.submit([task], owner)
    assert result is not None

    job_id = result[0].id

    # wait until the job is running
    while True:
        status: JobStatus = rti_proxy.get_job_status(job_id, owner)
        if status.state == JobStatus.State.RUNNING:
            break
        time.sleep(0.5)

    containers = docker_container_list()
    n0 = len(containers)

    # cancel the job (correct user)
    rti_proxy.cancel_job(job_id, owner)

    # give it a bit...
    time.sleep(5)

    containers = docker_container_list()
    n1 = len(containers)

    # the job should still be running because interrupt doesn't work
    assert n0 == n1

    # give it a bit more for the grace period to end...
    time.sleep(30)

    containers = docker_container_list()
    n2 = len(containers)

    # the job should be cancelled now because the container was killed
    assert n2 == n1 - 1


@pytest.mark.integration
def test_job_provenance_tracking(rti_context: RTIContext, test_context):
    """Test job provenance tracking through iterative computations."""
    from simaas.rti.base import RTIServiceBase

    rti: RTIServiceBase = rti_context.session_node.rti
    owner = rti_context.session_node.keystore

    def load_value(obj: DataObject) -> int:
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'temp.json')
            rti_context.dor_proxy.get_content(obj.obj_id, owner, path)
            with open(path, 'r') as f:
                content = json.load(f)
                value = content['v']
                return value

    # add test data object
    obj = rti_context.dor_proxy.add_data_object(
        test_context.create_file_with_content(f"{generate_random_string(4)}.json", json.dumps({'v': 1})),
        owner.identity, False, False, 'JSONObject', 'json'
    )

    # beginning
    obj_a = obj
    obj_b = obj
    value_a = load_value(obj_a)
    value_b = load_value(obj_b)

    # run 3 iterations
    log = []
    for i in range(3):
        job: Job = execute_job(
            rti_context.deployed_abc_processor.obj_id, owner, rti_context.rti_proxy,
            rti_context.session_node, a=obj_a, b=obj_b, memory=rti_context.default_memory
        )

        # wait until the job is done
        status: JobStatus = asyncio.run(rti.get_job_status(job.id))
        while status.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
            status: JobStatus = asyncio.run(rti.get_job_status(job.id))
            time.sleep(0.5)

        obj_c = status.output['c']
        value_c = load_value(obj_c)

        log.append(((value_a, value_b, value_c), (obj_a.c_hash, obj_b.c_hash, obj_c.c_hash)))

        obj_b = obj_c
        value_b = value_c

    for item in log:
        print(f"{item[1][0]} + {item[1][1]} = {item[1][2]}\t{item[0][0]} + {item[0][1]} = {item[0][2]}")

    # get the provenance and print it
    provenance = rti_context.dor_proxy.get_provenance(log[2][1][2])
    assert provenance is not None
    print(json.dumps(provenance.model_dump(), indent=2))


@pytest.mark.integration
@pytest.mark.slow
def test_job_concurrent_execution(rti_context: RTIContext, test_context, n: int = 20):
    """Test concurrent job execution with multiple simultaneous submissions."""
    from simaas.rti.base import RTIServiceBase

    wd_path = test_context.testing_dir
    owner = rti_context.session_node.keystore
    results = {}
    failed = {}
    logs = {}
    mutex = threading.Lock()
    rnd = random.Random()
    rti: RTIServiceBase = rti_context.session_node.rti

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
            job = execute_job(
                rti_context.deployed_abc_processor.obj_id, owner, rti_context.rti_proxy,
                rti_context.session_node, a=v0, b=v1, memory=rti_context.default_memory
            )
            logprint(idx, f"[{idx}] [{time.time()}] job {job.id} submitted: {os.path.join(rti._jobs_path, job.id)}")

            # wait until the job is done
            status: JobStatus = asyncio.run(rti.get_job_status(job.id))
            while status.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                status: JobStatus = asyncio.run(rti.get_job_status(job.id))
                time.sleep(1.0)

            logprint(idx, f"[{idx}] [{time.time()}] job {job.id} finished: {status.state}")

            if status.state != JobStatus.State.SUCCESSFUL:
                raise RuntimeError(f"[{idx}] failed: {status.state}")

            obj_id = status.output['c'].obj_id
            logprint(idx, f"[{idx}] obj_id: {obj_id}")

            download_path = os.path.join(wd_path, f"{obj_id}.json")
            while True:
                try:
                    logprint(idx, f"[{idx}] do fetch {obj_id}")
                    rti_context.dor_proxy.get_content(obj_id, owner, download_path)
                    logprint(idx, f"[{idx}] fetch returned {obj_id}")
                    break
                except UnsuccessfulRequestError as e:
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

    # submit jobs
    threads = []
    for i in range(n):
        thread = threading.Thread(target=do_a_job, kwargs={'idx': i})
        thread.start()
        threads.append(thread)

    # wait for all the threads
    for thread in threads:
        thread.join(60)

    for i in range(n):
        print(f"### {i} ###")
        log = logs[i]
        for msg in log:
            print(msg)
        print("###")

    for i, e in failed.items():
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        print(f"[{i}] failed: {trace}")

    logger.info(failed)
    assert len(failed) == 0
    assert len(results) == n


# ==============================================================================
# Batch Job Tests
# ==============================================================================

@pytest.mark.integration
def test_batch_submit_and_complete(rti_context: RTIContext, test_context, extra_keystores, n: int = 5):
    """Test batch job submission and completion."""
    proc_id = rti_context.deployed_abc_processor.obj_id
    wrong_user = rti_context.get_known_user(extra_keystores)
    owner = rti_context.session_node.keystore

    # Create batch tasks using TaskBuilder for named tasks
    tasks = [
        (TaskBuilder(proc_id, owner.identity.id)
         .with_name(f'task_{i}')
         .with_input_value('a', {'v': 1})
         .with_input_value('b', {'v': 5})
         .with_output('c', owner.identity.id)
         .with_budget(memory=rti_context.default_memory)
         .build())
        for i in range(n)
    ]

    # submit the batch
    jobs = rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert len(jobs) == len(tasks)

    # determine batch id
    batch_id = jobs[0].batch_id

    # try to get the batch info as the wrong user
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_context.rti_proxy.get_batch_status(batch_id, wrong_user)
    assert e.value.details['reason'] == 'user is not the batch owner, batch member or the node owner'

    # Wait for all batch members to complete
    while True:
        status: BatchStatus = rti_context.rti_proxy.get_batch_status(batch_id, owner)
        all_finished = all(
            member.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]
            for member in status.members
        )
        if all_finished:
            break
        time.sleep(1)

    # Verify all batch members completed with expected output
    for member in status.members:
        job_status: JobStatus = rti_context.rti_proxy.get_job_status(member.job_id, owner)
        assert_job_successful(job_status, expected_outputs=['c'])
        assert_data_object_content(
            rti_context.dor_proxy, job_status.output['c'].obj_id, owner,
            expected={'v': 6}, temp_dir=test_context.testing_dir
        )


@pytest.mark.integration
def test_batch_cancel_cascade(rti_context: RTIContext):
    """Test batch cancellation cascade when one job fails.

    NOTE: This test is Docker-only due to network limitations when testing AWS.
    While AWS jobs can reach the local custodian node (via SSH tunnel), the local
    node cannot reach jobs running on AWS's internal network to send P2P cancel
    signals. In production where the custodian runs on AWS infrastructure,
    cancellation works normally.
    """
    if rti_context.backend == 'aws':
        pytest.skip("Batch cancel cascade requires P2P connectivity to AWS jobs not available from local test environment")

    proc_id = rti_context.deployed_abc_processor.obj_id
    owner = rti_context.session_node.keystore

    # Create batch tasks using TaskBuilder
    tasks = [
        (TaskBuilder(proc_id, owner.identity.id)
         .with_name(f'task_{i}')
         .with_input_value('a', {'v': 30})
         .with_input_value('b', {'v': 30})
         .with_output('c', owner.identity.id)
         .with_budget(memory=rti_context.default_memory)
         .build())
        for i in range(10)
    ]

    # Modify the first task to trigger a failure (invalid values cause exception)
    # This should trigger the cancellation cascade.
    tasks[0].input[0].value = {'v': 5}
    tasks[0].input[1].value = {'v': 1000}

    # submit the batch
    jobs = rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert len(jobs) == len(tasks)

    # determine batch id
    batch_id = jobs[0].batch_id

    # Wait for all batch members to complete
    while True:
        status: BatchStatus = rti_context.rti_proxy.get_batch_status(batch_id, owner)
        all_finished = all(
            member.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]
            for member in status.members
        )
        if all_finished:
            break
        time.sleep(1)

    # Verify first job failed and others were cancelled
    for i, member in enumerate(status.members):
        job_status: JobStatus = rti_context.rti_proxy.get_job_status(member.job_id, owner)
        if i == 0:
            assert_job_failed(job_status)
        else:
            assert_job_cancelled(job_status)


# ==============================================================================
# Co-Simulation Tests
# ==============================================================================

@pytest.mark.integration
def test_cosim_duplicate_names(rti_context: RTIContext):
    """Test that co-simulation rejects duplicate task names."""
    owner = rti_context.session_node.keystore

    # get the co-sim tasks with duplicate names
    tasks = get_cosim_tasks(
        rti_context.deployed_room_processor, rti_context.deployed_thermostat_processor,
        owner.identity, duplicate_name='name', memory=rti_context.default_memory
    )

    # submit the job - should fail due to duplicate names
    try:
        rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
        assert False
    except UnsuccessfulRequestError as e:
        assert "Duplicate task name 'name'" in e.reason


@pytest.mark.integration
def test_cosim_room_thermostat(rti_context: RTIContext, test_context):
    """Test co-simulation with room and thermostat processors."""
    owner = rti_context.session_node.keystore

    # get the co-sim tasks
    tasks = get_cosim_tasks(
        rti_context.deployed_room_processor, rti_context.deployed_thermostat_processor,
        owner.identity, memory=rti_context.default_memory
    )

    # submit the job
    result = rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
    jobs = result

    batch_id = jobs[0].batch_id

    while True:
        try:
            status: BatchStatus = rti_context.rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)

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

    # get the result of the 'room' and the 'thermostat'
    job_status0: JobStatus = rti_context.rti_proxy.get_job_status(jobs[0].id, with_authorisation_by=owner)
    job_status1: JobStatus = rti_context.rti_proxy.get_job_status(jobs[1].id, with_authorisation_by=owner)
    assert 'result' in job_status0.output
    assert 'result' in job_status1.output

    # get the contents of the output data object
    download_path0 = os.path.join(test_context.testing_dir, 'result0.json')
    rti_context.dor_proxy.get_content(job_status0.output['result'].obj_id, owner, download_path0)
    assert os.path.isfile(download_path0)

    download_path1 = os.path.join(test_context.testing_dir, 'result1.json')
    rti_context.dor_proxy.get_content(job_status1.output['result'].obj_id, owner, download_path1)
    assert os.path.isfile(download_path1)

    # read the results
    with open(download_path0, 'r') as f:
        result0: dict = json.load(f)
        result0: RResult = RResult.model_validate(result0)

    with open(download_path1, 'r') as f:
        result1: dict = json.load(f)
        result1: TResult = TResult.model_validate(result1)

    # print the result
    print(result0.temp)
    print(result1.state)


# ==============================================================================
# Namespace Tests
# ==============================================================================

@pytest.mark.integration
def test_namespace_resource_limits(rti_context: RTIContext):
    """Test namespace resource limit enforcement."""
    owner = rti_context.session_node.keystore
    mem = rti_context.default_memory  # 1024 for Docker, 2048 for AWS

    # test with namespace that has not enough resources for a single task
    namespace0 = 'namespace0'
    asyncio.run(rti_context.session_node.db.update_namespace_budget(namespace0, ResourceDescriptor(vcpus=1, memory=mem // 2)))

    # get the tasks for namespace0 and try to submit jobs to namespace0 -> should fail
    tasks = get_cosim_tasks(
        rti_context.deployed_room_processor, rti_context.deployed_thermostat_processor,
        owner.identity, namespace0, memory=mem
    )
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert f"Task {tasks[0].name} exceeds namespace resource capacity" in e.value.reason

    # create namespaces with different resource budgets
    namespace1 = 'namespace1'
    namespace2 = 'namespace2'
    namespace3 = 'namespace3'
    asyncio.run(rti_context.session_node.db.update_namespace_budget(namespace1, ResourceDescriptor(vcpus=1, memory=mem)))
    asyncio.run(rti_context.session_node.db.update_namespace_budget(namespace2, ResourceDescriptor(vcpus=2, memory=mem)))
    asyncio.run(rti_context.session_node.db.update_namespace_budget(namespace3, ResourceDescriptor(vcpus=2, memory=mem * 2)))

    # get the tasks for namespace1 and try to submit jobs to namespace1 -> should fail
    tasks = get_cosim_tasks(
        rti_context.deployed_room_processor, rti_context.deployed_thermostat_processor,
        owner.identity, namespace1, memory=mem
    )
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert "Combined resource budget for namespace 'namespace1' exceeds namespace capacity" in e.value.reason

    # get the tasks for namespace2 and try to submit jobs to namespace2 -> should fail
    tasks = get_cosim_tasks(
        rti_context.deployed_room_processor, rti_context.deployed_thermostat_processor,
        owner.identity, namespace2, memory=mem
    )
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert "Combined resource budget for namespace 'namespace2' exceeds namespace capacity" in e.value.reason

    # get the tasks for namespace3 and try to submit jobs to namespace3 -> should succeed
    try:
        tasks = get_cosim_tasks(
            rti_context.deployed_room_processor, rti_context.deployed_thermostat_processor,
            owner.identity, namespace3, memory=mem
        )
        jobs: List[Job] = rti_context.rti_proxy.submit(tasks, with_authorisation_by=owner)
        batch_id = jobs[0].batch_id

        while True:
            time.sleep(1)
            status: BatchStatus = rti_context.rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)
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
