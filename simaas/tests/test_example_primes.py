import json
import logging
import os
import tempfile
import time

import pytest
from simaas.core.logging import Logging
from examples.prime.factor_search.processor import Parameters as FactorSearchParameters, ProcessorFactorSearch, \
    Result
from examples.prime.factorisation.processor import Parameters as FactorisationParameters, ProcessorFactorisation
from simaas.dor.schemas import DataObject
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import JobStatus, Task, Job, Processor
from simaas.tests.conftest import add_test_processor, BASE_DIR, DummyProgressListener

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


def test_proc_factor_search(dummy_namespace):
    """
    Test the `ProcessorFactorSearch` processor by computing all non-trivial factors of a given number (N).

    This test sets up a job to search for factors of 100 in a specified range, writes the input
    parameters to a file, executes the processor, and then validates the results against known factors.
    """
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
            result: dict = json.load(f)
            result: Result = Result.model_validate(result)

        print(result.factors)
        assert(result.factors == [2, 4, 5, 10, 20, 25, 50])
        if len(result.factors) == 0:
            print(f"N={N} is prime")
        else:
            print(f"N={N} is NOT prime")


def test_proc_factorisation(dummy_namespace):
    """
    Test the `ProcessorFactorisation` processor by running a full factorisation job with multiple sub-jobs.

    This test prepares a factorisation task for the number 100 with two sub-jobs, runs the processor,
    and verifies that the correct set of factors is returned.
    """
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
            result: dict = json.load(f)
            result: Result = Result.model_validate(result)

        print(result.factors)
        assert(result.factors == [2, 4, 5, 10, 20, 25, 50])
        if len(result.factors) == 2 and 1 in result.factors and N in result.factors:
            print(f"N={N} is prime")
        else:
            print(f"N={N} is NOT prime")


def test_proc_factorisation_cancel(dummy_namespace):
    """
    Test cancelling a long-running factorisation job via the RTI.

    This test submits a job to factor a large number, waits briefly, cancels it, and verifies
    that the job's final state is CANCELLED.
    """
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


@pytest.fixture(scope="session")
def deployed_factorisation_processor(
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
            dor_proxy, session_node.keystore, 'proc-factorisation', 'examples/prime/factorisation'
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
def deployed_factor_search_processor(
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
            dor_proxy, session_node.keystore, 'proc-factor-search', 'examples/prime/factor_search'
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


def test_factor_search_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_factor_search_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_factor_search_processor.obj_id
    owner = session_node.keystore

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'parameters', 'type': 'value', 'value': {
                    'start': 2,
                    'end': 100,
                    'number': 100
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
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )

    # submit the job
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
    assert ('result' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        result: Result = Result.model_validate(result)

    # print the result
    print(result.factors)
    assert (result.factors == [2, 4, 5, 10, 20, 25, 50])


def test_factorisation_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_factorisation_processor, deployed_factor_search_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_factorisation_processor.obj_id
    owner = session_node.keystore

    # submit the job
    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({
                'name': 'parameters', 'type': 'value', 'value': {
                    'N': 100,
                    'num_sub_jobs': 2
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
    assert ('result' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'result.json')
    dor_proxy.get_content(status.output['result'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        result: Result = Result.model_validate(result)

    # print the result
    print(result.factors)
    assert (result.factors == [2, 4, 5, 10, 20, 25, 50])
