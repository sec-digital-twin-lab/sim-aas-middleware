"""ABC Processor integration tests.

Tests for the ABC (simple arithmetic) processor, verifying both local execution
and Docker-based RTI execution with and without secrets.
"""

import json
import logging
import os
import tempfile
from typing import List

import pytest

from examples.simple.abc.processor import ProcessorABC, write_value
from simaas.core.logging import get_logger, initialise
from simaas.rti.schemas import JobStatus
from simaas.tests.fixture_core import BASE_DIR
from simaas.tests.fixture_mocks import DummyProgressListener
from simaas.tests.helper_waiters import wait_for_job_completion
from simaas.tests.helper_factories import create_abc_task
from simaas.tests.helper_assertions import assert_job_successful, assert_data_object_content

initialise(level=logging.DEBUG)
log = get_logger(__name__, 'test')


@pytest.mark.integration
def test_processor_abc_local_no_secret(dummy_namespace):
    """Test ABC processor local execution without secret environment variable."""
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
            ), dummy_namespace, logging.getLogger('test.abc')
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
    """Test ABC processor local execution with secret environment variable."""
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
        proc.run(temp_dir, None, DummyProgressListener(temp_dir, status, dummy_namespace.dor, expected_messages), dummy_namespace, logging.getLogger('test.abc'))

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
    """Test ABC processor job execution via RTI without secret."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    # Create and submit task using factory
    task = create_abc_task(proc_id, owner, a=1, b=2)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    # Wait for job completion
    status = wait_for_job_completion(rti_proxy, job.id, owner)

    # Verify job succeeded with expected output
    assert_job_successful(status, expected_outputs=['c'])
    assert_data_object_content(
        dor_proxy, status.output['c'].obj_id, owner,
        expected={'v': 3}, temp_dir=test_context.testing_dir
    )


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_abc_job_with_secret(
        docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_abc_processor
):
    """Test ABC processor job execution via RTI with secret."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    # define the secret
    os.environ['SECRET_ABC_KEY'] = '123'

    try:
        # Create and submit task using factory
        task = create_abc_task(proc_id, owner, a=1, b=2)
        result = rti_proxy.submit([task], with_authorisation_by=owner)
        job = result[0]

        # Wait for job completion
        status = wait_for_job_completion(rti_proxy, job.id, owner)

        # Verify job succeeded with expected output (secret value overrides sum)
        assert_job_successful(status, expected_outputs=['c'])
        assert_data_object_content(
            dor_proxy, status.output['c'].obj_id, owner,
            expected={'v': 123}, temp_dir=test_context.testing_dir
        )
    finally:
        # undefine the secret
        os.environ.pop('SECRET_ABC_KEY', None)
