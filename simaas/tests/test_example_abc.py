import json
import logging
import os
import tempfile
import time
from typing import List

import pytest

from examples.simple.abc.processor import ProcessorABC, write_value
from simaas.core.logging import Logging
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import JobStatus, Task
from simaas.tests.conftest import BASE_DIR, DummyProgressListener

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


def test_proc_abc_no_secret(dummy_namespace):
    with tempfile.TemporaryDirectory() as temp_dir:
        write_value(os.path.join(temp_dir, 'a'), 1)
        write_value(os.path.join(temp_dir, 'b'), 2)

        # undefine the secret (if necessary)
        if 'SECRET_ABC_KEY' in os.environ:
            os.environ.pop('SECRET_ABC_KEY')

        # what messages do we expect?
        expected_messages: List[str] = [
            "This is a message at the very beginning of the process.",
            "Environment variable SECRET_ABC_KEY is not defined.",
            "a=1",
            "b=2",
            "c=3",
            "...and we are done!"
        ]

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'simple', 'abc')
        proc = ProcessorABC(proc_path)
        proc.run(
            temp_dir, None, DummyProgressListener(
                temp_dir, status, dummy_namespace.dor, expected_messages
            ), dummy_namespace, None
        )

        # read the result
        c_path = os.path.join(temp_dir, 'c')
        assert os.path.isfile(c_path)
        with open(c_path, 'r') as f:
            result: dict = json.load(f)
            value = result['v']
            assert value == 3


def test_proc_abc_with_secret(dummy_namespace):
    with tempfile.TemporaryDirectory() as temp_dir:
        write_value(os.path.join(temp_dir, 'a'), 1)
        write_value(os.path.join(temp_dir, 'b'), 2)

        # define the secret
        os.environ['SECRET_ABC_KEY'] = '123'

        # what messages do we expect?
        expected_messages: List[str] = [
            "This is a message at the very beginning of the process.",
            "Environment variable SECRET_ABC_KEY is defined: '123'.",
            "a=1",
            "b=2",
            "c=123",
            "...and we are done!"
        ]

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'simple', 'abc')
        proc = ProcessorABC(proc_path)
        proc.run(temp_dir, None, DummyProgressListener(temp_dir, status, dummy_namespace.dor, expected_messages), dummy_namespace, None)

        # read the result
        c_path = os.path.join(temp_dir, 'c')
        assert os.path.isfile(c_path)
        with open(c_path, 'r') as f:
            result: dict = json.load(f)
            value = result['v']
            assert value == 123

        # undefine the secret
        os.environ.pop('SECRET_ABC_KEY')


def test_abc_submit_list_get_job_no_secret(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_abc_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    # submit the task
    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'a', 'type': 'value', 'value': {
                    'v': 1
                }
            }),
            Task.InputValue.model_validate({
                'name': 'b', 'type': 'value', 'value': {
                    'v': 2
                }
            }),
        ],
        output=[
            Task.Output.model_validate({
                'name': 'c',
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
    assert ('c' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'c.json')
    dor_proxy.get_content(status.output['c'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        value = result['v']
        assert value == 3


def test_abc_submit_list_get_job_with_secret(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_abc_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    # define the secret
    os.environ['SECRET_ABC_KEY'] = '123'

    # submit the task
    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'a', 'type': 'value', 'value': {
                    'v': 1
                }
            }),
            Task.InputValue.model_validate({
                'name': 'b', 'type': 'value', 'value': {
                    'v': 2
                }
            }),
        ],
        output=[
            Task.Output.model_validate({
                'name': 'c',
                'owner_iid': owner.identity.id,
                'restricted_access': False,
                'content_encrypted': False,
                'target_node_iid': None
            })
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None,
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
    assert ('c' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'c.json')
    dor_proxy.get_content(status.output['c'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        value = result['v']
        assert value == 123

    # undefine the secret
    os.environ.pop('SECRET_ABC_KEY')
