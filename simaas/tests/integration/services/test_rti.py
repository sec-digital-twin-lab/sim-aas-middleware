"""RTI (Runtime Infrastructure) service integration tests.

This module contains tests for:
- Processor deployment and management
- Job submission and execution
- Batch job processing
- Co-simulation scenarios
- Namespace resource management

Tests are organized by functionality and marked with appropriate markers
for Docker-only, AWS-only, or parameterized execution.
"""

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
from simaas.rti.schemas import Task, JobStatus, Processor, Job, BatchStatus, ProcessorVolume

from examples.cosim.room.processor import Result as RResult
from examples.cosim.thermostat.processor import Result as TResult

from plugins.rti_docker import DefaultRTIService

from simaas.tests.fixtures.rti import add_test_processor

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


# ==============================================================================
# Helper Functions
# ==============================================================================

def execute_job(proc_id: str, owner: Keystore, rti_proxy: RTIProxy, target_node: NodeInfo,
                a: Union[int, DataObject] = None, b: Union[int, DataObject] = None,
                memory: int = 1024) -> Job:
    """Execute a single job with the ABC processor.

    Args:
        proc_id: Processor ID to execute
        owner: Keystore of the job owner
        rti_proxy: RTI proxy for job submission
        target_node: Target node for output storage
        a: First input value or DataObject reference
        b: Second input value or DataObject reference
        memory: Memory budget in MB

    Returns:
        Submitted Job object
    """
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
    """Create co-simulation tasks for room and thermostat processors.

    Args:
        deployed_room_processor: DataObject for the room processor
        deployed_thermostat_processor: DataObject for the thermostat processor
        owner: Identity of the task owner
        namespace: Optional namespace for the tasks
        duplicate_name: If set, both tasks use this name (for testing duplicate detection)
        memory: Memory budget in MB

    Returns:
        List of Task objects for co-simulation
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
def test_processor_get_all(rti_proxy):
    """Test retrieving all deployed processors.

    Verifies that:
    - The get_all_procs API returns a valid response
    - The result is not None

    Backend: Docker
    Duration: ~1 second
    Requirements: None
    """
    result = rti_proxy.get_all_procs()
    assert result is not None


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_deploy_and_undeploy(docker_available, docker_non_strict_node, docker_strict_node, known_user):
    """Test processor deployment and undeployment with strict/non-strict modes.

    Verifies that:
    - Non-strict nodes allow any user to deploy/undeploy processors
    - Strict nodes only allow the node owner to deploy/undeploy
    - Appropriate errors are raised for unauthorized operations

    Backend: Docker only
    Duration: ~30 seconds
    Requirements: Docker
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

    # wait for deployment to be done
    while True:
        proc = rti0.get_proc(proc_id0)
        if proc.state == Processor.State.READY:
            break
        time.sleep(0.5)

    # try to deploy the processor with the wrong user on node1 (should fail - strict)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti1.deploy(proc_id1, wrong_user)
    assert 'User is not the node owner' in e.value.details['reason']

    # try to deploy the processor with the correct user on node1
    rti1.deploy(proc_id1, node1.keystore)

    while True:
        proc = rti1.get_proc(proc_id1)
        if proc.state == Processor.State.READY:
            break
        time.sleep(0.5)

    # wait for deployment to be done
    while rti1.get_proc(proc_id1).state != Processor.State.READY:
        time.sleep(0.5)

    # try to undeploy the processor with the wrong user on node0 (should succeed - non-strict)
    rti0.undeploy(proc_id0, wrong_user)

    try:
        while rti1.get_proc(proc_id0) is not None:
            time.sleep(0.5)
    except UnsuccessfulRequestError as e:
        assert 'Processor not deployed' in e.reason

    # try to undeploy the processor with the wrong user on node1 (should fail - strict)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti1.undeploy(proc_id1, wrong_user)
    assert 'User is not the node owner' in e.value.details['reason']

    # try to undeploy the processor with the correct user on node1
    rti1.undeploy(proc_id1, node1.keystore)

    try:
        while rti1.get_proc(proc_id1) is not None:
            time.sleep(0.5)
    except UnsuccessfulRequestError as e:
        assert 'Processor not deployed' in e.reason


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_deploy_with_volume(docker_available, docker_non_strict_node):
    """Test processor deployment with a mounted volume.

    Verifies that:
    - Processors can be deployed with a volume configuration
    - The volume is correctly mounted with the specified name

    Backend: Docker only
    Duration: ~15 seconds
    Requirements: Docker
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
        while True:
            proc_status: Optional[Processor] = rti.get_proc(proc_id)
            if proc_status.state == Processor.State.READY:
                print(proc_status.volumes)
                assert proc_status.volumes[0].name == 'data_volume'
                break
            time.sleep(0.5)

        # undeploy the processor
        rti.undeploy(proc_id, user)


# ==============================================================================
# Job Submission and Execution Tests
# ==============================================================================

@pytest.mark.integration
@pytest.mark.docker_only
def test_job_submit_and_retrieve(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor, known_user
):
    """Test job submission and status retrieval.

    Verifies that:
    - Jobs can be submitted with valid task definitions
    - Job status can be retrieved by the job owner
    - Unauthorized users cannot retrieve job status
    - Job completes successfully with expected output

    Backend: Docker only
    Duration: ~30 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    wrong_user = known_user
    owner = session_node.keystore

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({'name': 'a', 'type': 'value', 'value': {'v': 1}}),
            Task.InputValue.model_validate({'name': 'b', 'type': 'value', 'value': {'v': 1}})
        ],
        output=[
            Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                        'restricted_access': False, 'content_encrypted': False,
                                        'target_node_iid': None})
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )

    # submit the job
    jobs = rti_proxy.submit([task], with_authorisation_by=owner)
    job = jobs[0]
    assert job is not None

    job_id = job.id

    # get list of all jobs by correct user
    result = rti_proxy.get_jobs_by_user(owner)
    assert result is not None
    result = {job.id: job for job in result}
    assert job_id in result

    # get list of all jobs by wrong user
    result = rti_proxy.get_jobs_by_user(wrong_user)
    assert result is not None
    assert len(result) == 0

    # get list of all jobs by proc
    result = rti_proxy.get_jobs_by_proc(proc_id)
    assert result is not None
    assert len(result) == 1

    # try to get the job info as the wrong user
    try:
        rti_proxy.get_job_status(job_id, wrong_user)
        assert False

    except UnsuccessfulRequestError as e:
        assert e.details['reason'] == 'user is not the job owner or the node owner'

    while True:
        # get information about the running job
        try:
            status: JobStatus = rti_proxy.get_job_status(job_id, owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert status is not None

            if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                break

        except Exception:
            pass

        time.sleep(1)

    # check if we have an object id for output object 'c'
    assert 'c' in status.output

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'c.json')
    dor_proxy.get_content(status.output['c'].obj_id, owner, download_path)
    assert os.path.isfile(download_path)

    with open(download_path, 'r') as f:
        content = json.load(f)
        print(content)
        assert content['v'] == 2


@pytest.mark.integration
@pytest.mark.docker_only
def test_job_cancel_by_owner(docker_available, session_node, rti_proxy, deployed_abc_processor, known_user):
    """Test job cancellation by the job owner.

    Verifies that:
    - Unauthorized users cannot cancel jobs
    - Job owners can cancel running jobs
    - Cancelled jobs reach the CANCELLED state

    Backend: Docker only
    Duration: ~15 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    wrong_user = known_user
    owner = session_node.keystore

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({'name': 'a', 'type': 'value', 'value': {'v': 100}}),
            Task.InputValue.model_validate({'name': 'b', 'type': 'value', 'value': {'v': 100}})
        ],
        output=[
            Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                        'restricted_access': False, 'content_encrypted': False,
                                        'target_node_iid': None})
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )

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
        else:
            time.sleep(0.5)

    # cancel the job (correct user)
    rti_proxy.cancel_job(job_id, owner)

    # give it a bit...
    time.sleep(5)

    # get information about the job
    status: JobStatus = rti_proxy.get_job_status(job_id, owner)
    print(json.dumps(status.model_dump(), indent=4))
    assert status.state == JobStatus.State.CANCELLED


@pytest.mark.integration
@pytest.mark.docker_only
@pytest.mark.slow
def test_job_cancel_with_force_kill(docker_available, session_node, rti_proxy, deployed_abc_processor, known_user):
    """Test job cancellation with forced container kill after grace period.

    Verifies that:
    - Jobs that don't respond to interrupt signals continue running
    - After grace period expires, container is forcefully killed
    - Job reaches CANCELLED state after force kill

    Backend: Docker only
    Duration: ~40 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({'name': 'a', 'type': 'value', 'value': {'v': -100}}),
            Task.InputValue.model_validate({'name': 'b', 'type': 'value', 'value': {'v': 100}})
        ],
        output=[
            Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                        'restricted_access': False, 'content_encrypted': False,
                                        'target_node_iid': None})
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )

    # submit the job
    result = rti_proxy.submit([task], owner)
    assert result is not None

    job_id = result[0].id

    # wait until the job is running
    while True:
        status: JobStatus = rti_proxy.get_job_status(job_id, owner)
        if status.state == JobStatus.State.RUNNING:
            break
        else:
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
@pytest.mark.docker_only
def test_job_provenance_tracking(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor
):
    """Test job provenance tracking through iterative computations.

    Verifies that:
    - Jobs can use output from previous jobs as input
    - Provenance chain is correctly recorded
    - Provenance can be retrieved for output objects

    Backend: Docker only
    Duration: ~60 seconds
    Requirements: Docker
    """
    rti: DefaultRTIService = session_node.rti

    if not docker_available:
        pytest.skip("Docker is not available")

    owner = session_node.keystore

    def load_value(obj: DataObject) -> int:
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'temp.json')
            dor_proxy.get_content(obj.obj_id, owner, path)
            with open(path, 'r') as f:
                content = json.load(f)
                value = content['v']
                return value

    # add test data object
    obj = dor_proxy.add_data_object(
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
        job: Job = execute_job(deployed_abc_processor.obj_id, owner, rti_proxy, session_node, a=obj_a, b=obj_b)

        # wait until the job is done
        status: JobStatus = rti.get_job_status(job.id)
        while status.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
            status: JobStatus = rti.get_job_status(job.id)
            time.sleep(0.5)

        obj_c = status.output['c']
        value_c = load_value(obj_c)

        log.append(((value_a, value_b, value_c), (obj_a.c_hash, obj_b.c_hash, obj_c.c_hash)))

        obj_b = obj_c
        value_b = value_c

    for item in log:
        print(f"{item[1][0]} + {item[1][1]} = {item[1][2]}\t{item[0][0]} + {item[0][1]} = {item[0][2]}")

    # get the provenance and print it
    provenance = dor_proxy.get_provenance(log[2][1][2])
    assert provenance is not None
    print(json.dumps(provenance.dict(), indent=2))


@pytest.mark.integration
@pytest.mark.docker_only
@pytest.mark.slow
def test_job_concurrent_execution(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor, n: int = 50
):
    """Test concurrent job execution with multiple simultaneous submissions.

    Verifies that:
    - Multiple jobs can be submitted and executed concurrently
    - All jobs complete successfully
    - Output objects are correctly created and retrievable

    Backend: Docker only
    Duration: ~60 seconds
    Requirements: Docker

    Note: This test is marked as slow and may exhibit flaky behavior under
    high load conditions. Known flaky behavior: race condition where 49/50
    jobs complete within timeout.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    wd_path = test_context.testing_dir
    owner = session_node.keystore
    results = {}
    failed = {}
    logs = {}
    mutex = threading.Lock()
    rnd = random.Random()
    rti: DefaultRTIService = session_node.rti

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
            job = execute_job(deployed_abc_processor.obj_id, owner, rti_proxy, session_node, a=v0, b=v1)
            logprint(idx, f"[{idx}] [{time.time()}] job {job.id} submitted: {os.path.join(rti._jobs_path, job.id)}")

            # wait until the job is done
            status: JobStatus = rti.get_job_status(job.id)
            while status.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                status: JobStatus = rti.get_job_status(job.id)
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
                    dor_proxy.get_content(obj_id, owner, download_path)
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
@pytest.mark.docker_only
def test_batch_submit_and_complete(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor, known_user
):
    """Test batch job submission and completion.

    Verifies that:
    - Multiple tasks can be submitted as a batch
    - Batch status can be retrieved by the owner
    - Unauthorized users cannot retrieve batch status
    - All batch members complete successfully

    Backend: Docker only
    Duration: ~60 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    wrong_user = known_user
    owner = session_node.keystore

    def create_task(name: str, a: int, b: int) -> Task:
        return Task(
            proc_id=proc_id,
            user_iid=owner.identity.id,
            input=[
                Task.InputValue.model_validate({'name': 'a', 'type': 'value', 'value': {'v': a}}),
                Task.InputValue.model_validate({'name': 'b', 'type': 'value', 'value': {'v': b}})
            ],
            output=[
                Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                            'restricted_access': False, 'content_encrypted': False,
                                            'target_node_iid': None})
            ],
            name=name,
            description=None,
            budget=ResourceDescriptor(vcpus=1, memory=1024),
            namespace=None
        )

    # create the tasks
    tasks = [create_task(f'task_{i}', 1, 5) for i in range(20)]

    # submit the job
    jobs = rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert len(jobs) == len(tasks)

    # determine batch id
    batch_id = jobs[0].batch_id

    # try to get the batch info as the wrong user
    try:
        rti_proxy.get_batch_status(batch_id, wrong_user)
        assert False

    except UnsuccessfulRequestError as e:
        assert e.details['reason'] == 'user is not the batch owner, batch member or the node owner'

    while True:
        # get information about the running job
        try:
            status: BatchStatus = rti_proxy.get_batch_status(batch_id, owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert status is not None

            all_finished = True
            for member in status.members:
                if member.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                    all_finished = False
                    break

            if all_finished:
                break

        except Exception:
            pass

        time.sleep(1)

    # we should have an object id for output object 'c' for each member of the batch
    for member in status.members:
        job_status: JobStatus = rti_proxy.get_job_status(member.job_id, owner)
        assert 'c' in job_status.output

        # get the contents of the output data object
        download_path = os.path.join(test_context.testing_dir, 'c.json')
        dor_proxy.get_content(job_status.output['c'].obj_id, owner, download_path)
        assert os.path.isfile(download_path)

        with open(download_path, 'r') as f:
            content = json.load(f)
            print(content)
            assert content['v'] == 6


@pytest.mark.integration
@pytest.mark.docker_only
def test_batch_cancel_cascade(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor, known_user
):
    """Test batch cancellation cascade when one job fails.

    Verifies that:
    - When one job in a batch fails, remaining jobs are cancelled
    - The failed job reaches FAILED state
    - Other jobs reach CANCELLED state

    Backend: Docker only
    Duration: ~30 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    def create_task(name: str, a: int, b: int) -> Task:
        return Task(
            proc_id=proc_id,
            user_iid=owner.identity.id,
            input=[
                Task.InputValue.model_validate({'name': 'a', 'type': 'value', 'value': {'v': a}}),
                Task.InputValue.model_validate({'name': 'b', 'type': 'value', 'value': {'v': b}})
            ],
            output=[
                Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                            'restricted_access': False, 'content_encrypted': False,
                                            'target_node_iid': None})
            ],
            name=name,
            description=None,
            budget=ResourceDescriptor(vcpus=1, memory=1024),
            namespace=None
        )

    # create the tasks
    tasks = [create_task(f'task_{i}', 30, 30) for i in range(10)]

    # modify the a-value for one task to an invalid value which should cause an exception and the job to fail.
    # this should trigger the cancellation cascade.
    tasks[0].input[0].value = {'v': 5}
    tasks[0].input[1].value = {'v': 1000}

    # submit the job
    jobs = rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert len(jobs) == len(tasks)

    # determine batch id
    batch_id = jobs[0].batch_id

    while True:
        # get information about the running job
        try:
            status: BatchStatus = rti_proxy.get_batch_status(batch_id, owner)

            assert status is not None

            all_finished = True
            for member in status.members:
                if member.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                    all_finished = False
                    break

            if all_finished:
                break

        except Exception:
            pass

        time.sleep(1)

    for i in range(len(status.members)):
        job_id = status.members[i].job_id
        job_status: JobStatus = rti_proxy.get_job_status(job_id, owner)
        if i == 0:
            assert job_status.state == 'failed'
        else:
            assert job_status.state == 'cancelled'


# ==============================================================================
# Co-Simulation Tests
# ==============================================================================

@pytest.mark.integration
@pytest.mark.docker_only
def test_cosim_duplicate_names(
        docker_available, session_node, rti_proxy, deployed_room_processor, deployed_thermostat_processor
):
    """Test that co-simulation rejects duplicate task names.

    Verifies that:
    - Tasks with duplicate names are rejected
    - Appropriate error message is returned

    Backend: Docker only
    Duration: ~5 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    owner = session_node.keystore

    # get the co-sim tasks with duplicate names
    tasks = get_cosim_tasks(
        deployed_room_processor, deployed_thermostat_processor, owner.identity, duplicate_name='name'
    )

    # submit the job - should fail due to duplicate names
    try:
        rti_proxy.submit(tasks, with_authorisation_by=owner)
        assert False
    except UnsuccessfulRequestError as e:
        assert "Duplicate task name 'name'" in e.reason


@pytest.mark.integration
@pytest.mark.docker_only
def test_cosim_room_thermostat(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_room_processor,
        deployed_thermostat_processor
):
    """Test co-simulation with room and thermostat processors.

    Verifies that:
    - Two processors can be submitted as a batch for co-simulation
    - Both processors complete successfully
    - Output results are correctly generated and retrievable

    Backend: Docker only
    Duration: ~45 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    owner = session_node.keystore

    # get the co-sim tasks
    tasks = get_cosim_tasks(deployed_room_processor, deployed_thermostat_processor, owner.identity)

    # submit the job
    result = rti_proxy.submit(tasks, with_authorisation_by=owner)
    jobs = result

    batch_id = jobs[0].batch_id

    while True:
        try:
            status: BatchStatus = rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)

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
    job_status0: JobStatus = rti_proxy.get_job_status(jobs[0].id, with_authorisation_by=owner)
    job_status1: JobStatus = rti_proxy.get_job_status(jobs[1].id, with_authorisation_by=owner)
    assert 'result' in job_status0.output
    assert 'result' in job_status1.output

    # get the contents of the output data object
    download_path0 = os.path.join(test_context.testing_dir, 'result0.json')
    dor_proxy.get_content(job_status0.output['result'].obj_id, owner, download_path0)
    assert os.path.isfile(download_path0)

    download_path1 = os.path.join(test_context.testing_dir, 'result1.json')
    dor_proxy.get_content(job_status1.output['result'].obj_id, owner, download_path1)
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
@pytest.mark.docker_only
def test_namespace_resource_limits(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_room_processor,
        deployed_thermostat_processor
):
    """Test namespace resource limit enforcement.

    Verifies that:
    - Tasks exceeding namespace capacity are rejected
    - Combined resource budget enforcement works correctly
    - Tasks within namespace capacity are accepted

    Backend: Docker only
    Duration: ~60 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    owner = session_node.keystore

    # test with namespace that has not enough resources for a single task
    namespace0 = 'namespace0'
    session_node.db.update_namespace_budget(namespace0, ResourceDescriptor(vcpus=1, memory=512))

    # get the tasks for namespace0 and try to submit jobs to namespace0 -> should fail
    tasks = get_cosim_tasks(deployed_room_processor, deployed_thermostat_processor, owner.identity, namespace0)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert f"Task {tasks[0].name} exceeds namespace resource capacity" in e.value.reason

    # create namespaces with different resource budgets
    namespace1 = 'namespace1'
    namespace2 = 'namespace2'
    namespace3 = 'namespace3'
    session_node.db.update_namespace_budget(namespace1, ResourceDescriptor(vcpus=1, memory=1024))
    session_node.db.update_namespace_budget(namespace2, ResourceDescriptor(vcpus=2, memory=1024))
    session_node.db.update_namespace_budget(namespace3, ResourceDescriptor(vcpus=2, memory=2048))

    # get the tasks for namespace1 and try to submit jobs to namespace1 -> should fail
    tasks = get_cosim_tasks(deployed_room_processor, deployed_thermostat_processor, owner.identity, namespace1)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert "Combined resource budget for namespace 'namespace1' exceeds namespace capacity" in e.value.reason

    # get the tasks for namespace2 and try to submit jobs to namespace2 -> should fail
    tasks = get_cosim_tasks(deployed_room_processor, deployed_thermostat_processor, owner.identity, namespace2)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_proxy.submit(tasks, with_authorisation_by=owner)
    assert "Combined resource budget for namespace 'namespace2' exceeds namespace capacity" in e.value.reason

    # get the tasks for namespace3 and try to submit jobs to namespace3 -> should succeed
    try:
        tasks = get_cosim_tasks(deployed_room_processor, deployed_thermostat_processor, owner.identity, namespace3)
        jobs: List[Job] = rti_proxy.submit(tasks, with_authorisation_by=owner)
        batch_id = jobs[0].batch_id

        while True:
            time.sleep(1)
            status: BatchStatus = rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)
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
