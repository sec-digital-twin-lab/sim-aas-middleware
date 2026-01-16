"""Primes/Factorisation Processor integration tests.

Tests for the Factor Search and Factorisation processors, verifying
prime factor computation both locally and via Docker RTI.
"""

import json
import logging
import os
import tempfile
import time

import pytest

from examples.prime.factor_search.processor import Parameters as FactorSearchParameters, ProcessorFactorSearch, Result
from examples.prime.factorisation.processor import Parameters as FactorisationParameters, ProcessorFactorisation
from simaas.core.logging import Logging
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import JobStatus, Task, Job
from simaas.tests.fixture_core import BASE_DIR
from simaas.tests.fixture_mocks import DummyProgressListener
from simaas.tests.helper_waiters import wait_for_job_completion
from simaas.tests.helper_factories import TaskBuilder
from simaas.tests.helper_assertions import assert_job_successful

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.mark.integration
def test_processor_factor_search_local(dummy_namespace):
    """Test Factor Search processor local execution."""
    N = 100
    num_sub_jobs = 1
    step = N // num_sub_jobs
    i = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        # create parameters file
        start = 2 + i * step
        end = (i + 1) * step
        parameters = FactorSearchParameters(start=start, end=end, number=N)
        parameters_path = os.path.join(temp_dir, 'parameters')
        with open(parameters_path, 'w') as f:
            json.dump(parameters.model_dump(), f, indent=2)

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'prime', 'factor_search')
        proc = ProcessorFactorSearch(proc_path)
        proc.run(temp_dir, None, DummyProgressListener(temp_dir, status, dummy_namespace.dor), dummy_namespace, None)

        # read and validate the result
        result_path = os.path.join(temp_dir, 'result')
        assert os.path.isfile(result_path)
        with open(result_path, 'r') as f:
            result_data: dict = json.load(f)
            result: Result = Result.model_validate(result_data)

        print(result.factors)
        assert(result.factors == [2, 4, 5, 10, 20, 25, 50])
        if len(result.factors) == 0:
            print(f"N={N} is prime")
        else:
            print(f"N={N} is NOT prime")


@pytest.mark.integration
def test_processor_factorisation_local(dummy_namespace):
    """Test Factorisation processor local execution with multiple sub-jobs."""
    N = 100
    num_sub_jobs = 2

    with tempfile.TemporaryDirectory() as temp_dir:
        # create parameters file
        parameters = FactorisationParameters(N=N, num_sub_jobs=num_sub_jobs)
        parameters_path = os.path.join(temp_dir, 'parameters')
        with open(parameters_path, 'w') as f:
            json.dump(parameters.model_dump(), f, indent=2)

        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )

        # create the processor and run it
        proc_path = os.path.join(BASE_DIR, 'examples', 'prime', 'factorisation')
        proc = ProcessorFactorisation(proc_path)
        proc.run(temp_dir, None, DummyProgressListener(temp_dir, status, dummy_namespace.dor), dummy_namespace, None)

        # read and validate the result
        result_path = os.path.join(temp_dir, 'result')
        assert os.path.isfile(result_path)
        with open(result_path, 'r') as f:
            result_data: dict = json.load(f)
            result: Result = Result.model_validate(result_data)

        print(result.factors)
        assert(result.factors == [2, 4, 5, 10, 20, 25, 50])
        if len(result.factors) == 2 and 1 in result.factors and N in result.factors:
            print(f"N={N} is prime")
        else:
            print(f"N={N} is NOT prime")


@pytest.mark.integration
def test_processor_factorisation_cancel(dummy_namespace):
    """Test cancelling a long-running factorisation job."""
    N = 987654321987
    num_sub_jobs = 2

    task = Task(
        proc_id='factorisation',
        user_iid='someone',
        input=[Task.InputValue(
            name='parameters',
            type='value',
            value={
                'N': N,
                'num_sub_jobs': num_sub_jobs
            }
        )],
        output=[Task.Output(
            name='result',
            owner_iid='someone',
            restricted_access=False,
            content_encrypted=False,
            target_node_iid=None
        )],
        name=None,
        description=None,
        budget=None,
        namespace=None
    )
    result = dummy_namespace.rti.submit([task])
    job: Job = result[0]

    # wait for a bit to simulate processing time
    time.sleep(5)

    # cancel the job
    dummy_namespace.rti.job_cancel(job.id)

    # check that the job was cancelled
    status: JobStatus = dummy_namespace.rti.get_job_status(job.id)
    assert status.state == JobStatus.State.CANCELLED


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_factor_search_job(
        docker_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_factor_search_processor
):
    """Test Factor Search processor job execution via RTI."""
    if not docker_available:
        pytest.skip("Docker is not available")


    proc_id = deployed_factor_search_processor.obj_id
    owner = session_node.keystore

    # Create task using TaskBuilder
    task = (TaskBuilder(proc_id, owner.identity.id)
            .with_input_value('parameters', {'start': 2, 'end': 100, 'number': 100})
            .with_output('result', owner.identity.id)
            .build())

    # submit the job
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    # Wait for job completion
    status = wait_for_job_completion(rti_proxy, job.id, owner)

    # Verify job succeeded
    assert_job_successful(status, expected_outputs=['result'])

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert os.path.isfile(download_path)

    # read and validate the result
    with open(download_path, 'r') as f:
        result_data: dict = json.load(f)
        result: Result = Result.model_validate(result_data)

    print(result.factors)
    assert result.factors == [2, 4, 5, 10, 20, 25, 50]


@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_factorisation_job(
        docker_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_factorisation_processor, deployed_factor_search_processor
):
    """Test Factorisation processor job execution via RTI."""
    if not docker_available:
        pytest.skip("Docker is not available")


    proc_id = deployed_factorisation_processor.obj_id
    owner = session_node.keystore

    # Create task using TaskBuilder
    task = (TaskBuilder(proc_id, owner.identity.id)
            .with_input_value('parameters', {'N': 100, 'num_sub_jobs': 2})
            .with_output('result', owner.identity.id)
            .build())

    # submit the job
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    # Wait for job completion
    status = wait_for_job_completion(rti_proxy, job.id, owner)

    # Verify job succeeded
    assert_job_successful(status, expected_outputs=['result'])

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert os.path.isfile(download_path)

    # read and validate the result
    with open(download_path, 'r') as f:
        result_data: dict = json.load(f)
        result: Result = Result.model_validate(result_data)

    print(result.factors)
    assert result.factors == [2, 4, 5, 10, 20, 25, 50]
