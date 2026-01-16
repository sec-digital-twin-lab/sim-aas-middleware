import json
import os
import tempfile
import time
from typing import List

import pytest

from simaas.cli.cmd_image import PDIBuildLocal, PDIImport

from simaas.core.helpers import get_timestamp_now

from simaas.cli.cmd_rti import RTIProcDeploy, RTIProcList, RTIProcShow, RTIProcUndeploy, RTIJobSubmit, RTIJobStatus, \
    RTIJobList, RTIJobCancel, RTIVolumeCreateFSRef, RTIVolumeList, RTIVolumeDelete, RTIVolumeCreateEFSRef
from simaas.cli.exceptions import CLIRuntimeError
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.schemas import DataObject
from simaas.rti.schemas import Task, Job, JobStatus, Processor

logger = Logging.get(__name__)
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir



def test_cli_rti_volumes_list_add_delete(temp_dir):
    """Test CLI RTI volume management workflow."""
    try:
        cmd = RTIVolumeList()
        result = cmd.execute({
            'datastore': temp_dir,
        })
        assert result is not None
        assert len(result) == 0

    except CLIRuntimeError:
        assert False

    try:
        cmd = RTIVolumeCreateFSRef()
        cmd.execute({
            'datastore': temp_dir,
            'name': 'ref1',
            'path': os.path.join(temp_dir, 'does_not_exist')
        })

    except CLIRuntimeError:
        assert True

    try:
        cmd = RTIVolumeCreateFSRef()
        result = cmd.execute({
            'datastore': temp_dir,
            'name': 'ref1',
            'path': temp_dir
        })
        assert result is not None

    except CLIRuntimeError:
        assert False

    try:
        cmd = RTIVolumeList()
        result = cmd.execute({
            'datastore': temp_dir,
        })
        assert result is not None
        assert len(result) == 1

    except CLIRuntimeError:
        assert False

    try:
        cmd = RTIVolumeCreateEFSRef()
        result = cmd.execute({
            'datastore': temp_dir,
            'name': 'ref2',
            'efs_fs_id': 'fs-abc123'
        })
        assert result is not None

    except CLIRuntimeError:
        assert False

    try:
        cmd = RTIVolumeList()
        result = cmd.execute({
            'datastore': temp_dir,
        })
        assert result is not None
        assert len(result) == 2

    except CLIRuntimeError:
        assert False

    try:
        cmd = RTIVolumeDelete()
        result = cmd.execute({
            'datastore': temp_dir,
            'name': ['ref1']
        })
        assert result is not None
        assert len(result) == 1

    except CLIRuntimeError:
        assert False

    try:
        cmd = RTIVolumeList()
        result = cmd.execute({
            'datastore': temp_dir,
        })
        assert result is not None
        assert len(result) == 1

    except CLIRuntimeError:
        assert False



def test_cli_rti_proc_lifecycle(docker_available, session_node, temp_dir):
    """Test CLI RTI processor deployment lifecycle."""
    if not docker_available:
        pytest.skip("Docker is not available")

    address = session_node.rest.address()
    proc_abc_path = os.path.join(examples_path, 'simple', 'abc')

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)

    # ensure the node knows about this identity
    session_node.db.update_identity(keystore.identity)

    # build the PDI
    pdi_path = os.path.join(temp_dir, f"{get_timestamp_now()}.pdi")

    try:
        args = {
            'proc_path': proc_abc_path,
            'pdi_path': pdi_path,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64'
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result

    except CLIRuntimeError:
        assert False

    # import the PDI
    try:
        args = {
            'pdi_path': pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = session_node.dor.get_meta(pdi.obj_id)
        assert obj is not None
        assert obj.data_type == 'ProcessorDockerImage'
        assert obj.data_format == 'tar'

    except CLIRuntimeError:
        assert False

    # get list of deployed processors
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = RTIProcList()
        result = cmd.execute(args)
        assert result is not None
        assert 'deployed' in result
        n = len(result['deployed'])

    except CLIRuntimeError:
        assert False

    # deploy the processor
    try:
        args = {
            'datastore': temp_dir,
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': obj.obj_id,
        }

        cmd = RTIProcDeploy()
        result = cmd.execute(args)
        assert result is not None
        assert 'proc' in result
        assert result['proc'] is not None

    except CLIRuntimeError:
        assert False

    # get list of deployed processors
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = RTIProcList()
        result = cmd.execute(args)
        assert result is not None
        assert 'deployed' in result
        assert len(result['deployed']) == n + 1

    except CLIRuntimeError:
        assert False

    while True:
        # show the details of the deployed processor
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}",
                'proc_id': obj.obj_id
            }

            cmd = RTIProcShow()
            result = cmd.execute(args)
            assert result is not None
            assert 'processor' in result
            assert 'jobs' in result
            assert result['processor'] is not None
            assert len(result['jobs']) == 0

        except CLIRuntimeError:
            assert False

        proc: Processor = result['processor']
        if proc.state in [Processor.State.READY, Processor.State.FAILED]:
            break

        else:
            time.sleep(1)

    # undeploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': [obj.obj_id]
        }

        cmd = RTIProcUndeploy()
        result = cmd.execute(args)
        assert result is not None
        assert obj.obj_id in result

    except CLIRuntimeError:
        assert False

    time.sleep(1)

    # get list of deployed processors
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = RTIProcList()
        result = cmd.execute(args)
        assert result is not None
        assert 'deployed' in result
        assert len(result['deployed']) == n

    except CLIRuntimeError:
        assert False



def test_cli_rti_proc_volume(docker_available, session_node, temp_dir):
    """Test CLI RTI processor deployment with volume mounting."""
    if not docker_available:
        pytest.skip("Docker is not available")

    address = session_node.rest.address()
    proc_abc_path = os.path.join(examples_path, 'simple', 'abc')

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)

    # add a volume
    try:
        cmd = RTIVolumeCreateFSRef()
        cmd.execute({
            'datastore': temp_dir,
            'name': 'my_volume',
            'path': temp_dir
        })

    except CLIRuntimeError:
        assert True

    # ensure the node knows about this identity
    session_node.db.update_identity(keystore.identity)

    # build the PDI
    pdi_path = os.path.join(temp_dir, f"{get_timestamp_now()}.pdi")

    try:
        args = {
            'proc_path': proc_abc_path,
            'pdi_path': pdi_path,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64'
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result

    except CLIRuntimeError:
        assert False

    # import the PDI
    try:
        args = {
            'pdi_path': pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = session_node.dor.get_meta(pdi.obj_id)
        assert obj is not None
        assert obj.data_type == 'ProcessorDockerImage'
        assert obj.data_format == 'tar'

    except CLIRuntimeError:
        assert False

    # get list of deployed processors
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = RTIProcList()
        result = cmd.execute(args)
        assert result is not None
        assert 'deployed' in result
        n = len(result['deployed'])

    except CLIRuntimeError:
        assert False

    # deploy the processor
    try:
        args = {
            'datastore': temp_dir,
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': obj.obj_id,
            'volumes': [
                'my_volume:/mnt/storage:true'
            ]
        }

        cmd = RTIProcDeploy()
        result = cmd.execute(args)
        assert result is not None
        assert 'proc' in result
        assert result['proc'] is not None

    except CLIRuntimeError:
        assert False

    # get list of deployed processors
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = RTIProcList()
        result = cmd.execute(args)
        assert result is not None
        assert 'deployed' in result
        assert len(result['deployed']) == n + 1

    except CLIRuntimeError:
        assert False

    while True:
        # show the details of the deployed processor
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}",
                'proc_id': obj.obj_id
            }

            cmd = RTIProcShow()
            result = cmd.execute(args)
            assert result is not None
            assert 'processor' in result
            assert 'jobs' in result
            assert result['processor'] is not None
            assert len(result['jobs']) == 0

        except CLIRuntimeError:
            assert False

        proc: Processor = result['processor']
        if proc.state in [Processor.State.READY, Processor.State.FAILED]:
            break

        else:
            time.sleep(1)

    # undeploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': [obj.obj_id]
        }

        cmd = RTIProcUndeploy()
        result = cmd.execute(args)
        assert result is not None
        assert obj.obj_id in result

    except CLIRuntimeError:
        assert False

    time.sleep(1)

    # get list of deployed processors
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = RTIProcList()
        result = cmd.execute(args)
        assert result is not None
        assert 'deployed' in result
        assert len(result['deployed']) == n

    except CLIRuntimeError:
        assert False



def test_cli_rti_job_single(docker_available, session_node, temp_dir):
    """Test CLI RTI single job lifecycle (submit, list, status, cancel)."""
    if not docker_available:
        pytest.skip("Docker is not available")

    address = session_node.rest.address()
    proc_abc_path = os.path.join(examples_path, 'simple', 'abc')

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)

    # ensure the node knows about this identity
    session_node.db.update_identity(keystore.identity)

    # build the PDI
    pdi_path = os.path.join(temp_dir, f"{get_timestamp_now()}.pdi")

    try:
        args = {
            'proc_path': proc_abc_path,
            'pdi_path': pdi_path,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64'
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result

    except CLIRuntimeError:
        assert False

    # import the PDI
    try:
        args = {
            'pdi_path': pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = session_node.dor.get_meta(pdi.obj_id)
        assert obj is not None
        assert obj.data_type == 'ProcessorDockerImage'
        assert obj.data_format == 'tar'

    except CLIRuntimeError:
        assert False

    # deploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': obj.obj_id,
        }

        cmd = RTIProcDeploy()
        result = cmd.execute(args)
        assert result is not None
        assert 'proc' in result
        assert result['proc'] is not None

    except CLIRuntimeError:
        assert False

    # wait for processor to be deployed
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': obj.obj_id,
        }

        while True:
            cmd = RTIProcShow()
            result = cmd.execute(args)
            assert result is not None
            assert 'processor' in result
            assert result['processor'] is not None
            proc: Processor = result['processor']
            if proc.state in [Processor.State.READY, Processor.State.FAILED]:
                break

            time.sleep(1)

    except CLIRuntimeError:
        assert False

    # create task
    task_path = os.path.join(temp_dir, 'task.json')
    with open(task_path, 'w') as f:
        task = Task(
            proc_id=proc.id, user_iid=keystore.identity.id, name='test-task', description='',
            input=[
                Task.InputValue(name='a', type='value', value={'v': 10}),
                Task.InputValue(name='b', type='value', value={'v': 10})
            ],
            output=[
                Task.Output(name='c', owner_iid=keystore.identity.id, restricted_access=False,
                            content_encrypted=False, target_node_iid=session_node.identity.id)
            ],
            budget=None, namespace=None,
        )
        # noinspection PyTypeChecker
        json.dump(task.model_dump(), f, indent=2)

    # submit job
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'task': [task_path]
        }

        cmd = RTIJobSubmit()
        result = cmd.execute(args)
        assert result is not None
        assert 'job' in result
        assert result['job'] is not None
        job = result['job']

    except CLIRuntimeError:
        assert False

    def get_job_list() -> List[Job]:
        # get a list of all jobs
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}"
            }

            cmd = RTIJobList()
            result = cmd.execute(args)
            assert result is not None
            assert 'jobs' in result
            assert result['jobs'] is not None
            return result['jobs']

        except CLIRuntimeError:
            assert False

    def get_job_status(job_id: str) -> JobStatus:
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}",
                'job-id': job_id
            }

            cmd = RTIJobStatus()
            result = cmd.execute(args)
            assert result is not None
            assert 'status' in result
            assert result['status'] is not None
            return result['status']

        except CLIRuntimeError:
            assert False

    def wait_for_job_to_be_initialised(job_id: str, max_attempts: int = 20):
        for i in range(max_attempts):
            status: JobStatus = get_job_status(job_id)
            if status.state != JobStatus.State.UNINITIALISED:
                return
            else:
                time.sleep(0.5)
        assert False

    def wait_for_job_to_be_cancelled(job_id: str, max_attempts: int = 20):
        for i in range(max_attempts):
            status: JobStatus = get_job_status(job_id)
            if status.state == JobStatus.State.CANCELLED:
                return

            elif status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.FAILED]:
                assert False
            time.sleep(0.5)
        assert False

    def wait_for_jobs_to_be_cleared(max_attempts: int = 20):
        for i in range(max_attempts):
            jobs = get_job_list()
            if len(jobs) == 0:
                return
            time.sleep(0.5)
        assert False

    # we should have one job now
    jobs = get_job_list()
    assert(len(jobs)) == 1

    wait_for_job_to_be_initialised(job.id)

    # cancel the job
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'job-id': job.id
        }

        cmd = RTIJobCancel()
        result = cmd.execute(args)
        assert result is not None
        assert 'status' in result
        assert result['status'] is not None

    except CLIRuntimeError:
        assert False

    wait_for_job_to_be_cancelled(job.id)

    wait_for_jobs_to_be_cleared()

    # undeploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': [obj.obj_id],
            'force': True
        }

        cmd = RTIProcUndeploy()
        result = cmd.execute(args)
        assert result is not None
        assert obj.obj_id in result

    except CLIRuntimeError:
        assert False



def test_cli_rti_job_batch(docker_available, session_node, temp_dir, n=2):
    """Test CLI RTI batch job lifecycle (submit multiple, list, status, cancel)."""
    if not docker_available:
        pytest.skip("Docker is not available")

    address = session_node.rest.address()
    proc_abc_path = os.path.join(examples_path, 'simple', 'abc')

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)

    # ensure the node knows about this identity
    session_node.db.update_identity(keystore.identity)

    # build the PDI
    pdi_path = os.path.join(temp_dir, f"{get_timestamp_now()}.pdi")

    try:
        args = {
            'proc_path': proc_abc_path,
            'pdi_path': pdi_path,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64'
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result

    except CLIRuntimeError:
        assert False

    # import the PDI
    try:
        args = {
            'pdi_path': pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = session_node.dor.get_meta(pdi.obj_id)
        assert obj is not None
        assert obj.data_type == 'ProcessorDockerImage'
        assert obj.data_format == 'tar'

    except CLIRuntimeError:
        assert False

    # deploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': obj.obj_id,
        }

        cmd = RTIProcDeploy()
        result = cmd.execute(args)
        assert result is not None
        assert 'proc' in result
        assert result['proc'] is not None

    except CLIRuntimeError:
        assert False

    # wait for processor to be deployed
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': obj.obj_id,
        }

        while True:
            cmd = RTIProcShow()
            result = cmd.execute(args)
            assert result is not None
            assert 'processor' in result
            assert result['processor'] is not None
            proc: Processor = result['processor']
            if proc.state in [Processor.State.READY, Processor.State.FAILED]:
                break

            time.sleep(1)

    except CLIRuntimeError:
        assert False

    # create tasks
    tasks: List[str] = []
    for i in range(n):
        task_path = os.path.join(temp_dir, f'task_{i}.json')
        tasks.append(task_path)
        with open(task_path, 'w') as f:
            task = Task(
                proc_id=proc.id, user_iid=keystore.identity.id, name=f'test-task-{i}', description='',
                input=[
                    Task.InputValue(name='a', type='value', value={'v': 10}),
                    Task.InputValue(name='b', type='value', value={'v': 10})
                ],
                output=[
                    Task.Output(name='c', owner_iid=keystore.identity.id, restricted_access=False,
                                content_encrypted=False, target_node_iid=session_node.identity.id)
                ],
                budget=None,
                namespace=None
            )
            # noinspection PyTypeChecker
            json.dump(task.model_dump(), f, indent=2)

    # submit job
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'task': tasks
        }

        cmd = RTIJobSubmit()
        result = cmd.execute(args)
        assert result is not None
        assert 'jobs' in result
        assert len(result['jobs']) == n
        jobs = result['jobs']

    except CLIRuntimeError:
        assert False

    def get_job_list() -> List[Job]:
        # get a list of all jobs
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}"
            }

            cmd = RTIJobList()
            result = cmd.execute(args)
            assert result is not None
            assert 'jobs' in result
            assert result['jobs'] is not None
            return result['jobs']

        except CLIRuntimeError:
            assert False

    def get_job_status(job_id: str) -> JobStatus:
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}",
                'job-id': job_id
            }

            cmd = RTIJobStatus()
            result = cmd.execute(args)
            assert result is not None
            assert 'status' in result
            assert result['status'] is not None
            return result['status']

        except CLIRuntimeError:
            assert False

    def wait_for_job_to_be_initialised(job_id: str, max_attempts: int = 20):
        for i in range(max_attempts):
            status: JobStatus = get_job_status(job_id)
            if status.state != JobStatus.State.UNINITIALISED:
                return
            else:
                time.sleep(0.5)
        assert False

    def wait_for_job_to_be_cancelled(job_id: str, max_attempts: int = 20):
        for i in range(max_attempts):
            status: JobStatus = get_job_status(job_id)
            if status.state == JobStatus.State.CANCELLED:
                return

            elif status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.FAILED]:
                assert False
            time.sleep(0.5)
        assert False

    def wait_for_jobs_to_be_cleared(max_attempts: int = 20):
        for i in range(max_attempts):
            jobs = get_job_list()
            if len(jobs) == 0:
                return
            time.sleep(0.5)
        assert False

    # we should have n jobs
    assert len(get_job_list()) == n

    # wait for all jobs to be initialised
    for job in jobs:
        wait_for_job_to_be_initialised(job.id)

    # cancel the jobs
    for job in jobs:
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}",
                'job-id': job.id
            }

            cmd = RTIJobCancel()
            result = cmd.execute(args)
            assert result is not None
            assert 'status' in result
            assert result['status'] is not None

        except CLIRuntimeError:
            assert False

    # wait for all jobs to be cancelled
    for job in jobs:
        wait_for_job_to_be_cancelled(job.id)

    # wait for all jobs to be cleared
    wait_for_jobs_to_be_cleared()

    # undeploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc_id': [obj.obj_id],
            'force': True
        }

        cmd = RTIProcUndeploy()
        result = cmd.execute(args)
        assert result is not None
        assert obj.obj_id in result

    except CLIRuntimeError:
        assert False


