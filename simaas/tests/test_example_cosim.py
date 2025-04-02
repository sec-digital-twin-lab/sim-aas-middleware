import json
import logging
import os
import tempfile
import threading
import time

import pytest
from simaas.core.identity import Identity

from simaas.nodedb.schemas import NodeInfo

from simaas.core.logging import Logging
from examples.cosim.room.processor import Parameters as RParameters, RoomProcessor, Result as RResult
from examples.cosim.thermostat.processor import Parameters as TParameters, ThermostatProcessor, Result as TResult
from simaas.dor.schemas import DataObject
from simaas.rti.schemas import JobStatus, Task, Processor, BatchStatus, Job
from simaas.tests.conftest import add_test_processor, BASE_DIR, DummyProgressListener, DummyNamespace

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


def test_cosim(dummy_namespace):
    with tempfile.TemporaryDirectory() as temp_dir:
        user: Identity = dummy_namespace.keystore().identity

        # create working directories
        wd_path0 = os.path.join(temp_dir, 'room')
        wd_path1 = os.path.join(temp_dir, 'thermostat')
        os.makedirs(wd_path0, exist_ok=True)
        os.makedirs(wd_path1, exist_ok=True)

        # create parameters file
        p0 = RParameters(initial_temp=20, heating_rate=0.5, cooling_rate=-0.2, max_steps=100)
        p1 = TParameters(threshold_low=18.0, threshold_high=22.0)

        p_path0 = os.path.join(wd_path0, 'parameters')
        with open(p_path0, 'w') as f:
            json.dump(p0.model_dump(), f, indent=2)

        p_path1 = os.path.join(wd_path1, 'parameters')
        with open(p_path1, 'w') as f:
            json.dump(p1.model_dump(), f, indent=2)

        # create the processors and run them
        status0 = JobStatus(state=JobStatus.State.INITIALISED, progress=0, output={}, notes={}, errors=[], message=None)
        status1 = JobStatus(state=JobStatus.State.INITIALISED, progress=0, output={}, notes={}, errors=[], message=None)

        proc_path0 = os.path.join(BASE_DIR, 'examples', 'cosim', 'room')
        proc_path1 = os.path.join(BASE_DIR, 'examples', 'cosim', 'thermostat')
        proc0 = RoomProcessor(proc_path0)
        proc1 = ThermostatProcessor(proc_path1)

        custodian = NodeInfo(
            identity=user,
            last_seen=0,
            dor_service=False,
            rti_service=False,
            p2p_address='',
            rest_address=None,
            retain_job_history=False,
            strict_deployment=False
        )

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
                budget=None
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
                budget=None
            ),
            retain=False,
            custodian=custodian,
            proc_name='thermostat',
            t_submitted=0
        )

        # manually put the batch status in the dummy RTI
        rti: DummyNamespace.DummyRTI = dummy_namespace.rti
        rti.put_batch_status(BatchStatus(
            batch_id=batch_id,
            user_iid=user.id,
            members=[
                BatchStatus.Member(name='room', job_id=job0.id, state=JobStatus.State.INITIALISED, identity=user,
                                   ports={'7001/tcp': 'tcp://127.0.0.1:7001'}),
                BatchStatus.Member(name='thermostat', job_id=job1.id, state=JobStatus.State.INITIALISED, identity=user,
                                   ports={'7001/tcp': 'tcp://127.0.0.1:7002'})
            ]
        ))

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

        # read the results
        result_path0 = os.path.join(wd_path0, 'result')
        result_path1 = os.path.join(wd_path1, 'result')
        assert os.path.isfile(result_path0)
        assert os.path.isfile(result_path1)
        with open(result_path0, 'r') as f:
            result0: RResult = RResult.model_validate(json.load(f))
        with open(result_path1, 'r') as f:
            result1: TResult = TResult.model_validate(json.load(f))

        # print the result
        print(result0.temp)
        print(result1.state)


@pytest.fixture(scope="session")
def deployed_room_processor(
        docker_available, github_credentials_available, rti_proxy, dor_proxy, session_node
) -> DataObject:
    if not github_credentials_available:
        yield DataObject(
            obj_id='dummy',
            c_hash='dummy',
            data_type='dummy',
            data_format='dummy',
            created=DataObject.CreationDetails(timestamp=0, creators_iid=[]),
            owner_iid='dummy',
            access_restricted=False,
            access=[],
            tags={},
            last_accessed=0,
            custodian=None,
            content_encrypted=False,
            license=DataObject.License(by=False, sa=False, nc=False, nd=False),
            recipe=None
        )
    else:
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
def deployed_thermostat_processor(
        docker_available, github_credentials_available, rti_proxy, dor_proxy, session_node
) -> DataObject:
    if not github_credentials_available:
        yield DataObject(
            obj_id='dummy',
            c_hash='dummy',
            data_type='dummy',
            data_format='dummy',
            created=DataObject.CreationDetails(timestamp=0, creators_iid=[]),
            owner_iid='dummy',
            access_restricted=False,
            access=[],
            tags={},
            last_accessed=0,
            custodian=None,
            content_encrypted=False,
            license=DataObject.License(by=False, sa=False, nc=False, nd=False),
            recipe=None
        )
    else:
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


def test_cosim_search_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_room_processor, deployed_thermostat_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id0 = deployed_room_processor.obj_id
    proc_id1 = deployed_thermostat_processor.obj_id
    owner = session_node.keystore

    task0 = Task(
        proc_id=proc_id0,
        user_iid=owner.identity.id,
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
                'owner_iid': owner.identity.id,
                'restricted_access': False,
                'content_encrypted': False,
                'target_node_iid': None
            })
        ],
        name='room',
        description=None,
        budget=Task.Budget(vcpus=1, memory=1024)
    )

    task1 = Task(
        proc_id=proc_id1,
        user_iid=owner.identity.id,
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
                'owner_iid': owner.identity.id,
                'restricted_access': False,
                'content_encrypted': False,
                'target_node_iid': None
            })
        ],
        name='thermostat',
        description=None,
        budget=Task.Budget(vcpus=1, memory=1024)
    )

    # submit the job
    result = rti_proxy.submit([task0, task1], with_authorisation_by=owner)
    jobs = result

    batch_id = jobs[0].batch_id

    while True:
        try:
            status: BatchStatus = rti_proxy.get_batch_status(batch_id, with_authorisation_by=owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert (status is not None)

            is_done = True
            for member in status.members:
                if member.state not in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                    is_done = False

            if is_done:
                break

        except Exception as e:
            pass

        time.sleep(1)

    # check if we have an object id for output object 'c'
    assert ('result' in status.output)
    #
    # # get the contents of the output data object
    # download_path = os.path.join(test_context.testing_dir, 'result.json')
    # dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    # assert (os.path.isfile(download_path))
    #
    # # read the result
    # with open(download_path, 'r') as f:
    #     result: dict = json.load(f)
    #     result: Result = Result.model_validate(result)
    #
    # # print the result
    # print(result.factors)
    # assert (result.factors == [2, 4, 5, 10, 20, 25, 50])
