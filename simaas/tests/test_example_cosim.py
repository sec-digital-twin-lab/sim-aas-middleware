import json
import logging
import os
import tempfile
import threading
import time
import traceback

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
