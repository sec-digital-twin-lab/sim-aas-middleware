import json
import logging
import os
import tempfile
import threading
import time

import pytest
from simaas.dor.schemas import DataObject

from simaas.core.identity import Identity
from simaas.nodedb.schemas import NodeInfo, ResourceDescriptor
from simaas.core.logging import Logging
from examples.cosim.room.processor import Parameters as RParameters, RoomProcessor, Result as RResult
from examples.cosim.thermostat.processor import Parameters as TParameters, ThermostatProcessor, Result as TResult
from simaas.rti.schemas import JobStatus, Task, BatchStatus, Job, Processor
from simaas.tests.conftest import BASE_DIR, DummyProgressListener, DummyNamespace, add_test_processor

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


def test_cosim(dummy_namespace):
    """
    Test case for the co-simulation of Room and Thermostat processors.

    This test sets up two job processors — RoomProcessor and ThermostatProcessor — and runs them
    concurrently using simulated job descriptions and directories. It simulates thermal feedback control
    where the thermostat decides whether to heat the room based on temperature thresholds.

    The test verifies:
    - Creation and use of input parameter files.
    - Execution of both processors in separate threads.
    - Successful socket-based co-simulation between the two.
    - Proper generation of output result files.
    """

    with tempfile.TemporaryDirectory() as temp_dir:
        user: Identity = dummy_namespace.keystore().identity

        # Set up working directories for each processor
        wd_path0 = os.path.join(temp_dir, 'room')
        wd_path1 = os.path.join(temp_dir, 'thermostat')
        os.makedirs(wd_path0, exist_ok=True)
        os.makedirs(wd_path1, exist_ok=True)

        # Define input parameters for each processor
        p0 = RParameters(initial_temp=20, heating_rate=0.5, cooling_rate=-0.2, max_steps=100)
        p1 = TParameters(threshold_low=18.0, threshold_high=22.0)

        # Write parameter files to each processor's working directory
        p_path0 = os.path.join(wd_path0, 'parameters')
        with open(p_path0, 'w') as f:
            json.dump(p0.model_dump(), f, indent=2)

        p_path1 = os.path.join(wd_path1, 'parameters')
        with open(p_path1, 'w') as f:
            json.dump(p1.model_dump(), f, indent=2)

        # Initialize job status objects (used to track execution state)
        status0 = JobStatus(state=JobStatus.State.INITIALISED, progress=0, output={}, notes={}, errors=[], message=None)
        status1 = JobStatus(state=JobStatus.State.INITIALISED, progress=0, output={}, notes={}, errors=[], message=None)

        # Set paths to processor code directories
        proc_path0 = os.path.join(BASE_DIR, 'examples', 'cosim', 'room')
        proc_path1 = os.path.join(BASE_DIR, 'examples', 'cosim', 'thermostat')
        proc0 = RoomProcessor(proc_path0)
        proc1 = ThermostatProcessor(proc_path1)

        # Define dummy custodian node metadata
        custodian = NodeInfo(
            identity=user,
            last_seen=0,
            dor_service='none',
            rti_service='none',
            p2p_address='',
            rest_address=None,
            retain_job_history=False,
            strict_deployment=False
        )

        # Create Job and Task descriptors for both processors
        batch_id = 'batch123'
        job0 = Job(
            id='j0',
            batch_id=batch_id,
            task=Task(
                proc_id='proc-room',
                user_iid=user.id,
                input=[],
                output=[],
                name='room',
                description=None,
                budget=None,
                namespace=None
            ),
            retain=False,
            custodian=custodian,
            proc_name='room',
            t_submitted=0
        )
        job1 = Job(
            id='j1',
            batch_id=batch_id,
            task=Task(
                proc_id='proc-thermostat',
                user_iid=user.id,
                input=[],
                output=[],
                name='thermostat',
                description=None,
                budget=None,
                namespace=None
            ),
            retain=False,
            custodian=custodian,
            proc_name='thermostat',
            t_submitted=0
        )

        # Manually register the batch and job status with the dummy RTI
        rti: DummyNamespace.DummyRTI = dummy_namespace.rti
        rti.put_batch_status(BatchStatus(
            batch_id=batch_id,
            user_iid=user.id,
            members=[
                BatchStatus.Member(
                    name='room', job_id=job0.id,
                    state=JobStatus.State.INITIALISED,
                    identity=user,
                    ports={'7001/tcp': 'tcp://127.0.0.1:7001'}
                ),
                BatchStatus.Member(
                    name='thermostat', job_id=job1.id,
                    state=JobStatus.State.INITIALISED,
                    identity=user,
                    ports={'7001/tcp': 'tcp://127.0.0.1:7002'}
                )
            ]
        ))

        # Run both processors in parallel threads
        thread0 = threading.Thread(
            target=proc0.run,
            args=(wd_path0, job0, DummyProgressListener(wd_path0, status0, dummy_namespace.dor), dummy_namespace, None)
        )

        thread1 = threading.Thread(
            target=proc1.run,
            args=(wd_path1, job1, DummyProgressListener(wd_path1, status1, dummy_namespace.dor), dummy_namespace, None)
        )

        thread0.start()
        thread1.start()

        thread0.join()
        thread1.join()

        # Verify result files were generated
        result_path0 = os.path.join(wd_path0, 'result')
        result_path1 = os.path.join(wd_path1, 'result')
        assert os.path.isfile(result_path0)
        assert os.path.isfile(result_path1)

        # Load and validate the outputs
        with open(result_path0, 'r') as f:
            result0: RResult = RResult.model_validate(json.load(f))
        with open(result_path1, 'r') as f:
            result1: TResult = TResult.model_validate(json.load(f))

        # Print the result for inspection (e.g., in test output)
        print(result0.temp)   # list of room temperatures over time
        print(result1.state)  # list of [temperature, command] pairs from thermostat

@pytest.fixture(scope="session")
def deployed_room_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    # add test processor
    meta = add_test_processor(
        dor_proxy, session_node.keystore, 'proc-room', 'examples/cosim/room'
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


@pytest.fixture(scope="session")
def deployed_thermostat_processor(docker_available, rti_proxy, dor_proxy, session_node) -> DataObject:
    # add test processor
    meta = add_test_processor(
        dor_proxy, session_node.keystore, 'proc-thermostat', 'examples/cosim/thermostat'
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


def test_cosim_submit_list_get_job(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_room_processor,
        deployed_thermostat_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_room_id = deployed_room_processor.obj_id
    proc_thermostat_id = deployed_thermostat_processor.obj_id
    owner = session_node.keystore

    task0 = Task(
        proc_id=proc_room_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'parameters', 'type': 'value', 'value': {
                    "initial_temp": 15.0,
                    "heating_rate": 0.5,
                    "cooling_rate": -0.2,
                    "max_steps": 50
                }
            })
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
        name='room',
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )

    task1 = Task(
        proc_id=proc_thermostat_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'parameters', 'type': 'value', 'value': {
                    "threshold_low": 18.0,
                    "threshold_high": 22.0
                }
            })
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
        name='thermostat',
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )

    # submit the jobs
    result = rti_proxy.submit([task0, task1], with_authorisation_by=owner)
    assert len(result) == 2
    status_by_jobid = {}
    for job in result:
        while True:
            try:
                status: JobStatus = rti_proxy.get_job_status(job.id, owner)
                status_by_jobid[job.id] = status

                from pprint import pprint
                pprint(status.model_dump())
                assert (status is not None)

                if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                    break

            except Exception:
                pass

            time.sleep(1)

    # check if we have an object id for output object
    for status in status_by_jobid.values():
        assert status.state == JobStatus.State.SUCCESSFUL
