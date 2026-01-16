"""ABC Processor integration tests.

Tests for the ABC (simple arithmetic) processor, verifying both local execution
and Docker-based RTI execution with and without secrets.
"""

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
from simaas.tests.fixtures.core import BASE_DIR
from simaas.tests.fixtures.mocks import DummyProgressListener

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.mark.integration
def test_processor_abc_local_no_secret(dummy_namespace):
    """
    Test ABC processor local execution without secret environment variable.

    Verifies that:
    - Processor runs correctly with input values a=1 and b=2
    - Progress messages are sent in expected order
    - Output file 'c' is created with correct sum (3)

    Backend: Local (no Docker)
    Duration: ~1 second
    Requirements: None
    """
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


@pytest.mark.integration
def test_processor_abc_local_with_secret(dummy_namespace):
    """
    Test ABC processor local execution with secret environment variable.

    Verifies that:
    - Processor reads SECRET_ABC_KEY environment variable
    - Output is the secret value (123) instead of the sum
    - Progress messages reflect the secret being defined

    Backend: Local (no Docker)
    Duration: ~1 second
    Requirements: None
    """
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


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_abc_job_no_secret(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor
):
    """
    Test ABC processor job execution via RTI without secret.

    Verifies that:
    - Job can be submitted with input values
    - Job completes successfully
    - Output data object is created with correct sum

    Backend: Docker
    Duration: ~30 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

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


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_abc_job_with_secret(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor
):
    """
    Test ABC processor job execution via RTI with secret.

    Verifies that:
    - Job can be submitted with input values
    - SECRET_ABC_KEY environment variable affects output
    - Output data object contains secret value instead of sum

    Backend: Docker
    Duration: ~30 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

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
