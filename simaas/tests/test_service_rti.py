import json
import logging
import os
import random
import tempfile
import threading
import time
import traceback
from typing import Union

import pytest

from simaas.core.helpers import generate_random_string
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.api import DORProxy
from simaas.dor.schemas import DataObject
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import NodeInfo
from simaas.rest.exceptions import UnsuccessfulRequestError
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Task, JobStatus, Processor
from simaas.tests.conftest import add_test_processor, REPOSITORY_URL

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture(scope='session')
def non_strict_node(test_context):
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("non_strict_node", "no-email-provided", path=tempdir, password="password")
        keystore.github_credentials.update(
            REPOSITORY_URL,
            GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
        )
        _node = test_context.get_node(keystore, use_rti=True, enable_rest=True, strict_deployment=False)
        yield _node


@pytest.fixture(scope='session')
def strict_node(test_context, extra_keystores):
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("strict_node", "no-email-provided", path=tempdir, password="password")
        keystore.github_credentials.update(
            REPOSITORY_URL,
            GithubCredentials(login=os.environ['GITHUB_USERNAME'], personal_access_token=os.environ['GITHUB_TOKEN'])
        )
        _node = test_context.get_node(keystore, use_rti=True, enable_rest=True, strict_deployment=True)
        yield _node


@pytest.fixture()
def known_user(extra_keystores, node_db_proxy):
    _keystore = extra_keystores[2]
    node_db_proxy.update_identity(_keystore.identity)
    return _keystore


def test_rest_get_deployed(rti_proxy):
    result = rti_proxy.get_all_procs()
    assert (result is not None)


def test_rest_deploy_undeploy(docker_available, non_strict_node, strict_node, known_user):
    if not docker_available:
        pytest.skip("Docker is not available")

    node0 = non_strict_node
    db0 = NodeDBProxy(node0.rest.address())
    dor0 = DORProxy(node0.rest.address())
    rti0 = RTIProxy(node0.rest.address())

    node1 = strict_node
    db1 = NodeDBProxy(node1.rest.address())
    dor1 = DORProxy(node1.rest.address())
    rti1 = RTIProxy(node1.rest.address())

    # check flags
    info0 = db0.get_node()
    info1 = db1.get_node()
    assert (info0.strict_deployment is False)
    assert (info1.strict_deployment is True)

    # upload the test proc GCC
    proc0: DataObject = add_test_processor(dor0, node0.keystore)
    proc_id0 = proc0.obj_id
    proc1: DataObject = add_test_processor(dor1, node1.keystore)
    proc_id1 = proc1.obj_id

    # make the wrong user identity known to the nodes
    wrong_user = known_user
    db0.update_identity(wrong_user.identity)
    db1.update_identity(wrong_user.identity)

    # try to deploy the processor with the wrong user on node0
    rti0.deploy(proc_id0, wrong_user)

    # wait for deployment to be done
    while True:
        proc = rti0.get_proc(proc_id0)
        if proc.state == Processor.State.READY:
            break
        time.sleep(0.5)

    # try to deploy the processor with the wrong user on node1
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti1.deploy(proc_id1, wrong_user)
    assert ('User is not the node owner' in e.value.details['reason'])

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

    # try to undeploy the processor with the wrong user on node0
    rti0.undeploy(proc_id0, wrong_user)

    try:
        while rti1.get_proc(proc_id0) is not None:
            time.sleep(0.5)
    except UnsuccessfulRequestError as e:
        assert ('Processor not deployed' in e.reason)

    # try to undeploy the processor with the wrong user on node1
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti1.undeploy(proc_id1, wrong_user)
    assert ('User is not the node owner' in e.value.details['reason'])

    # try to undeploy the processor with the correct user on node1
    rti1.undeploy(proc_id1, node1.keystore)

    try:
        while rti1.get_proc(proc_id1) is not None:
            time.sleep(0.5)
    except UnsuccessfulRequestError as e:
        assert ('Processor not deployed' in e.reason)


def test_rest_submit_list_get_job(docker_available, test_context, node, dor_proxy, rti_proxy, deployed_test_processor,
                                  known_user):
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_test_processor.obj_id
    wrong_user = known_user
    owner = node.keystore

    task_input = [
        Task.InputValue.parse_obj({'name': 'a', 'type': 'value', 'value': {'v': 1}}),
        Task.InputValue.parse_obj({'name': 'b', 'type': 'value', 'value': {'v': 1}})
    ]

    task_output = [
        Task.Output.parse_obj({'name': 'c', 'owner_iid': owner.identity.id,
                               'restricted_access': False, 'content_encrypted': False,
                               'target_node_iid': None})
    ]

    # submit the job
    result = rti_proxy.submit_job(proc_id, task_input, task_output, owner)
    assert (result is not None)

    job_id = result.id

    # get list of all jobs by correct user
    result = rti_proxy.get_jobs_by_user(owner)
    assert (result is not None)
    result = {job.id: job for job in result}
    assert (job_id in result)

    # get list of all jobs by wrong user
    result = rti_proxy.get_jobs_by_user(wrong_user)
    assert (result is not None)
    assert (len(result) == 0)

    # get list of all jobs by proc
    result = rti_proxy.get_jobs_by_proc(proc_id)
    assert (result is not None)
    assert (len(result) == 1)

    # try to get the job info as the wrong user
    try:
        rti_proxy.get_job_status(job_id, wrong_user)
        assert False

    except UnsuccessfulRequestError as e:
        assert (e.details['reason'] == 'user is not the job owner or the node owner')

    while True:
        # get information about the running job
        try:
            status: JobStatus = rti_proxy.get_job_status(job_id, owner)

            from pprint import pprint
            pprint(status.dict())
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

    with open(download_path, 'r') as f:
        content = json.load(f)
        print(content)
        assert (content['v'] == 2)


def test_rest_submit_cancel_job(docker_available, node, rti_proxy, deployed_test_processor, known_user):
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_test_processor.obj_id
    wrong_user = known_user
    owner = node.keystore

    task_input = [
        Task.InputValue.parse_obj({'name': 'a', 'type': 'value', 'value': {'v': 100}}),
        Task.InputValue.parse_obj({'name': 'b', 'type': 'value', 'value': {'v': 100}})
    ]

    task_output = [
        Task.Output.parse_obj({'name': 'c', 'owner_iid': owner.identity.id,
                               'restricted_access': False, 'content_encrypted': False,
                               'target_node_iid': None})
    ]

    # submit the job
    result = rti_proxy.submit_job(proc_id, task_input, task_output, owner)
    assert (result is not None)

    job_id = result.id

    # try to cancel the job (wrong user)
    with pytest.raises(UnsuccessfulRequestError) as e:
        rti_proxy.cancel_job(job_id, wrong_user)
    assert ('user is not the job owner' in e.value.details['reason'])

    # wait until the job is running
    while True:
        status: JobStatus = rti_proxy.get_job_status(job_id, owner)
        if status.state == JobStatus.State.RUNNING:
            break
        else:
            time.sleep(0.5)

    # cancel the job (correct user)
    rti_proxy.cancel_job(job_id, owner)

    # get information about the job
    status: JobStatus = rti_proxy.get_job_status(job_id, owner)
    print(json.dumps(status.dict(), indent=4))
    assert (status.state == JobStatus.State.CANCELLED)


def execute_job(proc_id: str, owner: Keystore, rti_proxy: RTIProxy, target_node: NodeInfo,
                a: Union[int, DataObject] = None, b: Union[int, DataObject] = None) -> JobStatus:

    if a is None:
        a = 1

    if b is None:
        b = 1

    a = Task.InputReference(name='a', type='reference', obj_id=a.obj_id, user_signature=None, c_hash=None) \
        if isinstance(a, DataObject) else Task.InputValue(name='a', type='value', value={'v': a})

    b = Task.InputReference(name='b', type='reference', obj_id=b.obj_id, user_signature=None, c_hash=None) \
        if isinstance(b, DataObject) else Task.InputValue(name='b', type='value', value={'v': b})

    task_input = [a, b]

    task_output = [
        Task.Output.parse_obj({'name': 'c', 'owner_iid': owner.identity.id,
                               'restricted_access': False, 'content_encrypted': False,
                               'target_node_iid': target_node.identity.id})
    ]

    # submit the job
    job = rti_proxy.submit_job(proc_id, task_input, task_output, owner)

    # wait until the job is done
    while True:
        try:
            status: JobStatus = rti_proxy.get_job_status(job.id, owner)
            if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                return status

        except Exception:
            pass

        time.sleep(0.5)


def test_provenance(docker_available, test_context, node, dor_proxy, rti_proxy, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    owner = node.keystore

    def load_value(obj: DataObject) -> int:
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'temp.json')
            dor_proxy.get_content(obj.obj_id, owner, path)
            with open(path, 'r') as f:
                content = json.load(f)
                value = content['v']
                return value

    # add test data object
    obj = dor_proxy.add_data_object(test_context.create_file_with_content(f"{generate_random_string(4)}.json",
                                                                          json.dumps({'v': 1})),
                                    owner.identity, False, False, 'JSONObject', 'json')

    # beginning
    obj_a = obj
    obj_b = obj
    value_a = load_value(obj_a)
    value_b = load_value(obj_b)

    # run 3 iterations
    log = []
    for i in range(3):
        status = execute_job(deployed_test_processor.obj_id, owner, rti_proxy, node, a=obj_a, b=obj_b)

        obj_c = status.output['c']
        value_c = load_value(obj_c)

        log.append(((value_a, value_b, value_c), (obj_a.c_hash, obj_b.c_hash, obj_c.c_hash)))

        obj_b = obj_c
        value_b = value_c

    for item in log:
        print(f"{item[1][0]} + {item[1][1]} = {item[1][2]}\t{item[0][0]} + {item[0][1]} = {item[0][2]}")

    # get the provenance and print it
    provenance = dor_proxy.get_provenance(log[2][1][2])
    assert (provenance is not None)
    print(json.dumps(provenance.dict(), indent=2))


def test_job_concurrency(docker_available, test_context, node, dor_proxy, rti_proxy, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    wd_path = test_context.testing_dir
    owner = node.keystore
    results = {}
    failed = {}
    rnd = random.Random()

    def do_a_job(idx: int) -> None:
        try:
            dt = rnd.randint(0, 1000) / 1000.0
            v0 = rnd.randint(2, 6)
            v1 = rnd.randint(2, 6)

            time.sleep(dt)

            print(f"[{idx}] [{time.time()}] submit job")
            status = execute_job(deployed_test_processor.obj_id, owner, rti_proxy, node, a=v0, b=v1)
            print(f"[{idx}] proc status: {status}")

            obj_id = status.output['c'].obj_id
            download_path = os.path.join(wd_path, f"{obj_id}.json")
            while True:
                try:
                    dor_proxy.get_content(obj_id, owner, download_path)
                    break
                except UnsuccessfulRequestError as e:
                    print(e)
                    time.sleep(0.5)

            with open(download_path, 'r') as f:
                content = json.load(f)
                results[idx] = content['v']

        except Exception as e:
            failed[idx] = e

    # submit jobs
    n = 50
    threads = []
    for i in range(n):
        thread = threading.Thread(target=do_a_job, kwargs={'idx': i})
        thread.start()
        threads.append(thread)

    # wait for all the threads
    for thread in threads:
        thread.join()

    for idx, e in failed.items():
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        logger.error(f"[{idx}] failed: {trace}")

    # print(results)
    logger.info(failed)
    assert (len(failed) == 0)
    assert (len(results) == n)
