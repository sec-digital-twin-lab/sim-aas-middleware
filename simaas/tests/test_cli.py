import json
import logging
import multiprocessing
import os
import socket
import tempfile
import threading
import time
import traceback
from typing import Tuple, List, Union, Any, Optional

import pytest
from docker.errors import ImageNotFound

from examples.adapters.proc_example.processor import write_value
from simaas.cli.cmd_dor import DORAdd, DORMeta, DORDownload, DORRemove, DORSearch, DORTag, DORUntag, DORAccessShow, \
    DORAccessGrant, DORAccessRevoke
from simaas.cli.cmd_identity import IdentityCreate, IdentityList, IdentityRemove, IdentityShow, IdentityDiscover, \
    IdentityPublish, IdentityUpdate, CredentialsList, CredentialsAddGithubCredentials, CredentialsRemove
from simaas.cli.cmd_job_runner import JobRunner
from simaas.cli.cmd_network import NetworkList
from simaas.cli.cmd_proc_builder import clone_repository, build_processor_image, ProcBuilder
from simaas.cli.cmd_rti import RTIProcDeploy, RTIProcList, RTIProcShow, RTIProcUndeploy, RTIJobSubmit, RTIJobStatus, \
    RTIJobList, RTIJobCancel
from simaas.cli.exceptions import CLIRuntimeError
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.dor.schemas import DataObject, ProcessorDescriptor, GitProcessorPointer
from simaas.helpers import find_available_port, docker_export_image, PortMaster
from simaas.node.base import Node
from simaas.rti.api import JobRESTProxy
from simaas.rti.schemas import Task, Job, JobStatus, Severity, ExitCode, JobResult, Processor
from simaas.core.processor import ProgressListener, ProcessorBase, ProcessorRuntimeError, find_processors
from simaas.tests.conftest import REPOSITORY_COMMIT_ID, REPOSITORY_URL

logger = Logging.get(__name__)
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def test_cli_identity_list_create_show_remove(temp_dir):
    # list all identities
    try:
        args = {
            'keystore': temp_dir,
        }

        cmd = IdentityList()
        result = cmd.execute(args)
        assert result is not None
        assert 'available' in result
        assert len(result['available']) == 0

    except CLIRuntimeError:
        assert False

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIRuntimeError:
        assert False

    # list all identities
    try:
        args = {
            'keystore': temp_dir,
        }

        cmd = IdentityList()
        result = cmd.execute(args)
        assert result is not None
        assert 'available' in result
        assert len(result['available']) == 1

    except CLIRuntimeError:
        assert False

    # show identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id
        }

        cmd = IdentityShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'content' in result
        assert result['content'] is not None

    except CLIRuntimeError:
        assert False

    # remove the identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'confirm': True
        }

        cmd = IdentityRemove()
        result = cmd.execute(args)
        assert result is None

    except CLIRuntimeError:
        assert False

    # list all identities
    try:
        args = {
            'keystore': temp_dir,
        }

        cmd = IdentityList()
        result = cmd.execute(args)
        assert result is not None
        assert 'available' in result
        assert len(result['available']) == 0

    except CLIRuntimeError:
        assert False


def test_cli_identity_discover_publish_update(node, temp_dir):
    address = node.rest.address()

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIRuntimeError:
        assert False

    # discover all identities known to the node
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityDiscover()
        result = cmd.execute(args)
        assert result is not None
        assert keystore.identity.id not in result

    except CLIRuntimeError:
        assert False

    # publish identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityPublish()
        result = cmd.execute(args)
        assert result is None

    except CLIRuntimeError:
        assert False

    # discover all identities known to the node
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityDiscover()
        result = cmd.execute(args)
        assert result is not None
        assert keystore.identity.id in result

    except CLIRuntimeError:
        assert False

    # update identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'name': 'updated_name'
        }

        cmd = IdentityUpdate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        assert keystore.identity.name == 'updated_name'

    except CLIRuntimeError:
        assert False

    # publish identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityPublish()
        result = cmd.execute(args)
        assert result is None

    except CLIRuntimeError:
        assert False

    # discover all identities known to the node
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityDiscover()
        result = cmd.execute(args)
        assert result is not None
        assert keystore.identity.id in result

        identity: Identity = result[keystore.identity.id]
        assert identity.name == 'updated_name'

    except CLIRuntimeError:
        assert False


def test_cli_identity_credentials_list_add_remove(temp_dir):
    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIRuntimeError:
        assert False

    # list all credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
        }

        cmd = CredentialsList()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert len(result['credentials']) == 0

    except CLIRuntimeError:
        assert False

    # add credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'url': 'url',
            'login': 'login',
            'personal_access_token': 'personal-access-token'
        }

        cmd = CredentialsAddGithubCredentials()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert result['credentials'] is not None

    except CLIRuntimeError:
        assert False

    # list all credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
        }

        cmd = CredentialsList()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert len(result['credentials']) == 1

    except CLIRuntimeError:
        assert False

    # remove credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'credential': 'github:url',
            'confirm': True
        }

        cmd = CredentialsRemove()
        result = cmd.execute(args)
        assert result is not None
        assert 'removed' in result
        assert len(result['removed']) == 1

    except CLIRuntimeError:
        assert False

    # list all credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
        }

        cmd = CredentialsList()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert len(result['credentials']) == 0

    except CLIRuntimeError:
        assert False


def test_cli_network_show(node, temp_dir):
    address = node.rest.address()

    # get network information
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = NetworkList()
        result = cmd.execute(args)
        assert result is not None
        assert 'network' in result
        assert len(result['network']) == 1

    except CLIRuntimeError:
        assert False


def test_cli_dor_add_meta_download_tag_search_untag_remove(node, temp_dir):
    address = node.rest.address()

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIRuntimeError:
        assert False

    # add a data object
    try:
        file_path = os.path.join(temp_dir, 'test.json')
        with open(file_path, 'w') as f:
            json.dump({
                'test': 1
            }, f, indent=2)

        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'restrict_access': False,
            'content_encrypted': False,
            'assume_creator': True,
            'data-type': 'JSON',
            'data-format': 'json',
            'file': [file_path]
        }

        cmd = DORAdd()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None
        obj: DataObject = result['obj']

    except CLIRuntimeError:
        assert False

    # get data object meta information
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORMeta()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None

    except CLIRuntimeError:
        assert False

    # download data object content
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'destination': temp_dir,
            'obj-ids': [obj.obj_id],
        }

        cmd = DORDownload()
        result = cmd.execute(args)
        assert result is not None
        assert len(result) == 1
        assert obj.obj_id in result
        assert os.path.isfile(result[obj.obj_id])

    except CLIRuntimeError:
        assert False

    # tag the data object
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id,
            'tags': ['aaa=1', 'bbb=2']
        }

        cmd = DORTag()
        result = cmd.execute(args)
        assert result is not None
        assert obj.obj_id in result
        assert 'aaa' in result[obj.obj_id].tags
        assert 'bbb' in result[obj.obj_id].tags

    except CLIRuntimeError:
        assert False

    # search for data object
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'own': None,
            'data-type': None,
            'data-format': None,
            'pattern': ['aaa']
        }

        cmd = DORSearch()
        result = cmd.execute(args)
        assert result is not None
        assert len(result) == 1
        assert obj.obj_id in result

    except CLIRuntimeError:
        assert False

    # untag the data object
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id,
            'keys': ['aaa']
        }

        cmd = DORUntag()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None
        assert result['obj'].obj_id == obj.obj_id
        assert 'aaa' not in result['obj'].tags
        assert 'bbb' in result['obj'].tags

    except CLIRuntimeError:
        assert False

    # search for data object
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'own': None,
            'data-type': None,
            'data-format': None,
            'pattern': ['aaa']
        }

        cmd = DORSearch()
        result = cmd.execute(args)
        assert result is not None
        assert len(result) == 0

    except CLIRuntimeError:
        assert False

    # remove data object
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-ids': [obj.obj_id],
            'confirm': True
        }

        cmd = DORRemove()
        result = cmd.execute(args)
        assert result is not None
        assert 'removed' in result
        assert result['removed'] is not None
        assert obj.obj_id in result['removed']

    except CLIRuntimeError:
        assert False


def test_cli_dor_grant_show_revoke(node, temp_dir):
    address = node.rest.address()

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIRuntimeError:
        assert False

    # add a data object
    try:
        file_path = os.path.join(temp_dir, 'test.json')
        with open(file_path, 'w') as f:
            json.dump({
                'test': 1
            }, f, indent=2)

        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'restrict_access': False,
            'content_encrypted': False,
            'assume_creator': True,
            'data-type': 'JSON',
            'data-format': 'json',
            'file': [file_path]
        }

        cmd = DORAdd()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None
        obj: DataObject = result['obj']

    except CLIRuntimeError:
        assert False

    # show the access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORAccessShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'access' in result
        assert len(result['access']) == 1
        assert keystore.identity.id in result['access']

    except CLIRuntimeError:
        assert False

    # revoke access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id,
            'iids': [keystore.identity.id]
        }

        cmd = DORAccessRevoke()
        result = cmd.execute(args)
        assert result is not None
        assert 'revoked' in result
        assert len(result['revoked']) == 1

    except CLIRuntimeError:
        assert False

    # show the access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORAccessShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'access' in result
        assert len(result['access']) == 0

    except CLIRuntimeError:
        assert False

    # grant access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'iid': keystore.identity.id,
            'obj-ids': [obj.obj_id],
        }

        cmd = DORAccessGrant()
        result = cmd.execute(args)
        assert result is not None
        assert 'granted' in result
        assert len(result['granted']) == 1

    except CLIRuntimeError:
        assert False

    # show the access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORAccessShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'access' in result
        assert len(result['access']) == 1
        assert keystore.identity.id in result['access']

    except CLIRuntimeError:
        assert False


def prepare_data_object(content_path: str, node: Node, v: int = 1, data_type: str = 'JSONObject',
                        data_format: str = 'json', access: List[Identity] = None) -> DataObject:
    with open(content_path, 'w') as f:
        json.dump({'v': v}, f, indent=2)

    proxy = DORProxy(node.rest.address())
    if access:
        obj = proxy.add_data_object(content_path, node.identity, True, False, data_type, data_format)
        for identity in access:
            obj = proxy.grant_access(obj.obj_id, node.keystore, identity)

    else:
        obj = proxy.add_data_object(content_path, node.identity, False, False, data_type, data_format)

    return obj


def prepare_plain_job_folder(jobs_root_path: str, job_id: str, a: Any = 1, b: Any = 1) -> str:
    # create the job folder
    job_path = os.path.join(jobs_root_path, job_id)
    os.makedirs(job_path, exist_ok=True)

    # write the data objects 'a' and 'b'
    write_value(os.path.join(job_path, 'a'), a)
    write_value(os.path.join(job_path, 'b'), b)

    return job_path


def prepare_full_job_folder(jobs_root_path: str, node: Node, user: Identity, proc: DataObject, job_id: str,
                            a: Union[dict, int, str, DataObject], b: Union[dict, int, str, DataObject],
                            sig_a: str = None, sig_b: str = None, target_node: Node = None) -> str:
    proc_descriptor = ProcessorDescriptor.parse_obj(proc.tags['proc_descriptor'])

    if a is None:
        a = {'v': 1}
    elif isinstance(a, (int, str)):
        a = {'v': a}

    if b is None:
        b = {'v': 1}
    elif isinstance(b, (int, str)):
        b = {'v': b}

    a = Task.InputReference(name='a', type='reference', obj_id=a.obj_id, user_signature=sig_a, c_hash=None) \
        if isinstance(a, DataObject) else Task.InputValue(name='a', type='value', value=a)

    b = Task.InputReference(name='b', type='reference', obj_id=b.obj_id, user_signature=sig_b, c_hash=None) \
        if isinstance(b, DataObject) else Task.InputValue(name='b', type='value', value=b)

    c = Task.Output(name='c', owner_iid=user.id, restricted_access=False, content_encrypted=False,
                    target_node_iid=target_node.identity.id if target_node else node.identity.id)

    task = Task(proc_id=proc.obj_id, user_iid=user.id, input=[a, b], output=[c], name='test', description='')

    # create job
    job = Job(id=job_id, task=task, retain=False, custodian=node.info, proc_name=proc_descriptor.name, t_submitted=0)

    # create gpp
    gpp = GitProcessorPointer(repository=proc.tags['repository'], commit_id=proc.tags['commit_id'],
                              proc_path=proc.tags['proc_path'], proc_descriptor=proc_descriptor)

    # create the job folder
    job_path = os.path.join(jobs_root_path, job.id)
    os.makedirs(job_path, exist_ok=True)

    # write job descriptor
    job_descriptor_path = os.path.join(job_path, 'job.descriptor')
    with open(job_descriptor_path, 'w') as f:
        json.dump(job.dict(), f, indent=2)

    # write gpp descriptor
    gpp_descriptor_path = os.path.join(job_path, 'gpp.descriptor')
    with open(gpp_descriptor_path, 'w') as f:
        json.dump(gpp.dict(), f, indent=2)

    return job_path


def run_job_cmd(job_path: str, host: str, port: int) -> None:
    cmd = JobRunner()
    args = {
        'job_path': job_path,
        'proc_path': examples_path,
        'proc_name': 'example-processor',
        'rest_address': f"{host}:{port}"
    }
    cmd.execute(args)


def run_job_cmd_noname(job_path: str, host: str, port: int) -> None:
    cmd = JobRunner()
    args = {
        'job_path': job_path,
        'proc_path': os.path.join(examples_path, 'adapters', 'proc_example'),
        'rest_address': f"{host}:{port}"
    }
    cmd.execute(args)


class ProcessorRunner(threading.Thread, ProgressListener):
    def __init__(self, proc: ProcessorBase, wd_path: str, log_level: int = logging.INFO) -> None:
        super().__init__()

        self._mutex = threading.Lock()
        self._proc = proc
        self._wd_path = wd_path
        self._interrupted = False

        # setup logger
        log_path = os.path.join(wd_path, 'job.log')
        self._logger = Logging.get('cli.job_runner', level=log_level, custom_log_path=log_path)

        # initialise job status
        self._job_status = JobStatus(state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={},
                                     errors=[], message=None)
        self._store_job_status()

    def on_progress_update(self, progress: int) -> None:
        self._logger.info(f"on_progress_update: progress={progress}")
        self._job_status.progress = progress
        self._store_job_status()

    def on_output_available(self, output_name: str) -> None:
        if output_name not in self._job_status.output:
            self._logger.info(f"on_output_available: output_name={output_name}")
            self._job_status.output[output_name] = None
            self._store_job_status()

    def on_message(self, severity: Severity, message: str) -> None:
        self._logger.info(f"on_message: severity={severity} message={message}")
        self._job_status.message = JobStatus.Message(severity=severity, content=message)
        self._store_job_status()

    def _store_job_status(self) -> None:
        job_status_path = os.path.join(self._wd_path, 'job.status')
        with open(job_status_path, 'w') as f:
            json.dump(self._job_status.dict(), f, indent=2)

    def _write_exitcode(self, exitcode: ExitCode, e: Exception = None) -> None:
        exitcode_path = os.path.join(self._wd_path, 'job.exitcode')
        with open(exitcode_path, 'w') as f:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            result = JobResult(exitcode=exitcode, trace=trace)
            json.dump(result.dict(), f, indent=2)

    def run(self) -> None:
        try:
            self._logger.info(f"begin processing job at {self._wd_path}")

            self._proc.run(self._wd_path, self, self._logger)

            if self._interrupted:
                self._logger.info(f"end processing job at {self._wd_path} -> INTERRUPTED")
                self._write_exitcode(ExitCode.INTERRUPTED)

            else:
                self._logger.info(f"end processing job at {self._wd_path} -> DONE")
                self._write_exitcode(ExitCode.DONE)

        except ProcessorRuntimeError as e:
            self._logger.error(f"end processing job at {self._wd_path} -> FAILED: {e.reason}")
            self._write_exitcode(ExitCode.ERROR, e)

        except Exception as e:
            self._logger.error(f"end processing job at {self._wd_path} -> FAILED: {e}")
            self._write_exitcode(ExitCode.ERROR, e)

    def status(self) -> JobStatus:
        with self._mutex:
            return self._job_status

    def interrupt(self) -> JobStatus:
        with self._mutex:
            self._logger.info(f"attempt to interrupt job at {self._wd_path}...")
            self._interrupted = True
            self._proc.interrupt()
            return self._job_status


def wait_for_job_runner(job_path: str, rest_address: (str, int)) -> Tuple[Optional[JobResult], Optional[JobStatus]]:
    job_exitcode_path = os.path.join(job_path, 'job.exitcode')
    job_status_path = os.path.join(job_path, 'job.status')
    proxy = JobRESTProxy(rest_address)
    while True:
        status: JobStatus = proxy.job_status()
        if status is None:
            # is there a job.exitcode and runner.exitcode file?
            has_job_exitcode = os.path.isfile(job_exitcode_path)
            has_job_status = os.path.isfile(job_status_path)
            if has_job_exitcode:
                job_result = JobResult.parse_file(job_exitcode_path) if has_job_exitcode else None
                status = JobStatus.parse_file(job_status_path) if has_job_status else None
                return job_result, status

            else:
                pass

        else:
            pass

        time.sleep(0.5)


def test_job_worker_done(temp_dir):
    job_id = 'abcd1234_00'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 1, 1)

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('example-processor')
    assert(proc is not None)

    worker = ProcessorRunner(proc, job_path, logging.INFO)
    worker.start()
    worker.join()

    expected_files = ['c', 'job.exitcode', 'job.status', 'job.log']
    for file in expected_files:
        if not os.path.isfile(os.path.join(job_path, file)):
            assert False

    exitcode_path = os.path.join(job_path, 'job.exitcode')
    with open(exitcode_path, 'r') as f:
        result = JobResult.parse_obj(json.load(f))

    assert result.exitcode == ExitCode.DONE


def test_job_worker_interrupted(temp_dir):
    job_id = 'abcd1234_01'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 5, 5)

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('example-processor')
    assert(proc is not None)

    worker = ProcessorRunner(proc, job_path, logging.INFO)
    worker.start()
    worker.interrupt()
    worker.join()

    expected_files = ['job.exitcode', 'job.status', 'job.log']
    for file in expected_files:
        if not os.path.isfile(os.path.join(job_path, file)):
            assert False

    exitcode_path = os.path.join(job_path, 'job.exitcode')
    with open(exitcode_path, 'r') as f:
        result = JobResult.parse_obj(json.load(f))

    assert result.exitcode == ExitCode.INTERRUPTED


def test_job_worker_error(temp_dir):
    job_id = 'abcd1234_02'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 1, 'sdf')

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('example-processor')
    assert(proc is not None)

    worker = ProcessorRunner(proc, job_path, logging.INFO)
    worker.start()
    worker.join()

    expected_files = ['job.exitcode', 'job.status', 'job.log']
    for file in expected_files:
        if not os.path.isfile(os.path.join(job_path, file)):
            assert False

    exitcode_path = os.path.join(job_path, 'job.exitcode')
    with open(exitcode_path, 'r') as f:
        result = JobResult.parse_obj(json.load(f))

    assert result.exitcode == ExitCode.ERROR
    assert "ValueError: invalid literal for int() with base 10: 'sdf'" in result.trace


def test_cli_runner_success_by_value(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare the job folder
    job_id = '398h36g3_00'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=1, b=1)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 100
    assert job_result.exitcode == ExitCode.DONE


def test_cli_runner_failing_validation(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare the job folder
    job_id = '398h36g3_01'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id,
                                       a={'wrong': 55}, b=1)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 0
    assert job_result.exitcode == ExitCode.ERROR
    assert 'InvalidJSONDataObjectError' in job_result.trace


def test_cli_runner_success_by_reference(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1)

    # prepare the job folder
    job_id = '398h36g3_02'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=a, b=b)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 100
    assert job_result.exitcode == ExitCode.DONE


def test_cli_runner_failing_no_access(docker_available, temp_dir, node, deployed_test_processor, extra_keystores):
    if not docker_available:
        pytest.skip("Docker is not available")

    user = extra_keystores[0]
    node.db.update_identity(user.identity)

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1, access=[node.identity])
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1, access=[node.identity])

    # prepare the job folder
    job_id = '398h36g3_03'
    job_path = prepare_full_job_folder(temp_dir, node, user.identity, deployed_test_processor, job_id, a=a, b=b)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 0
    assert job_result.exitcode == ExitCode.ERROR
    assert 'AccessNotPermittedError' in job_result.trace


def test_cli_runner_failing_no_signature(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1, access=[node.identity])
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1, access=[node.identity])

    # prepare the job folder
    job_id = '398h36g3_04'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=a, b=b)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 0
    assert job_result.exitcode == ExitCode.ERROR
    assert 'MissingUserSignatureError' in job_result.trace


def test_cli_runner_failing_no_data_object(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1)

    # delete the object so it can't be found
    proxy = DORProxy(node.rest.address())
    proxy.delete_data_object(b.obj_id, node.keystore)

    # prepare the job folder
    job_id = '398h36g3_05'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=a, b=b)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 0
    assert job_result.exitcode == ExitCode.ERROR
    assert 'UnresolvedInputDataObjectsError' in job_result.trace


def test_cli_runner_failing_wrong_data_type(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1, data_type='wrong')
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1)

    # prepare the job folder
    job_id = '398h36g3_06'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=a, b=b)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 0
    assert job_result.exitcode == ExitCode.ERROR
    assert 'MismatchingDataTypeOrFormatError' in job_result.trace


def test_cli_runner_failing_wrong_data_format(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1, data_format='wrong')
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1)

    # prepare the job folder
    job_id = '398h36g3_07'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=a, b=b)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 0
    assert job_result.exitcode == ExitCode.ERROR
    assert 'MismatchingDataTypeOrFormatError' in job_result.trace


def test_cli_runner_success_no_name(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare the job folder
    job_id = '398h36g3_08'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=1, b=1)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd_noname, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.progress == 100
    assert job_result.exitcode == ExitCode.DONE


def test_cli_runner_cancelled(docker_available, temp_dir, node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare the job folder
    job_id = '398h36g3_09'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id, a=5, b=6)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # try to cancel the job
    proxy = JobRESTProxy(rest_address)
    while proxy.job_cancel() is None:
       time.sleep(1)

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert job_result.exitcode == ExitCode.INTERRUPTED


def test_cli_runner_success_non_dor_target(docker_available, temp_dir, node, exec_only_node, deployed_test_processor):
    if not docker_available:
        pytest.skip("Docker is not available")

    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), node, 1)

    # prepare the job folder
    job_id = '398h36g3_10'
    job_path = prepare_full_job_folder(temp_dir, node, node.identity, deployed_test_processor, job_id,
                                       a=a, b=b, target_node=exec_only_node)

    # determine REST address
    rest_address = PortMaster.generate_rest_address()

    # execute the job runner command
    job_process = multiprocessing.Process(target=run_job_cmd, args=(job_path, rest_address[0], rest_address[1]))
    job_process.start()

    # wait for the job to be finished
    job_result, status = wait_for_job_runner(job_path, rest_address)
    assert status.state == JobStatus.State.FAILED
    assert "Pushing output data object 'c' failed." in status.errors[0].message
    assert status.errors[0].exception.reason == 'Target node does not support DOR capabilities'
    assert job_result.exitcode == ExitCode.ERROR


def test_find_open_port():
    # block port 5995
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('localhost', 5995))
    server_socket.listen(1)

    port = find_available_port(host='localhost', port_range=(5990, 5994))
    assert(port == 5990)

    port = find_available_port(host='localhost', port_range=(5995, 5999))
    assert(port == 5996)


def test_cli_builder_clone_repo(temp_dir):
    credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])
    repo_path = os.path.join(temp_dir, 'repository')

    try:
        clone_repository(REPOSITORY_URL+"_doesnt_exist", temp_dir, credentials=credentials)
        assert False
    except CLIRuntimeError:
        assert True

    try:
        clone_repository(REPOSITORY_URL, repo_path, commit_id="doesntexist", credentials=credentials)
        assert False
    except CLIRuntimeError:
        assert os.path.isdir(repo_path)
        assert True

    try:
        clone_repository(REPOSITORY_URL, repo_path, commit_id=REPOSITORY_COMMIT_ID, credentials=credentials)
        assert os.path.isdir(repo_path)
        assert True
    except CLIRuntimeError:
        assert False


def test_cli_builder_build_image(docker_available, temp_dir):
    if not docker_available:
        pytest.skip("Docker is not available")

    # clone the repository
    credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])
    repo_path = os.path.join(temp_dir, 'repository')
    clone_repository(REPOSITORY_URL, repo_path, commit_id=REPOSITORY_COMMIT_ID, credentials=credentials)

    proc_path = "examples/adapters/proc_example"

    try:
        build_processor_image(repo_path+"_wrong", proc_path)
        assert False
    except CLIRuntimeError:
        assert True

    try:
        proc_path_wrong = "examples/adapters"
        build_processor_image(repo_path, proc_path_wrong)
        assert False
    except CLIRuntimeError:
        assert True

    try:
        build_processor_image(repo_path, proc_path)
    except CLIRuntimeError:
        assert False


def test_cli_builder_export_image(docker_available, temp_dir):
    if not docker_available:
        pytest.skip("Docker is not available")

    image_path = os.path.join(temp_dir, 'image.tar')

    try:
        docker_export_image('doesnt-exist', image_path)
        assert False
    except ImageNotFound:
        assert True

    # clone the repository
    credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])
    repo_path = os.path.join(temp_dir, 'repository')
    clone_repository(REPOSITORY_URL, repo_path, commit_id=REPOSITORY_COMMIT_ID, credentials=credentials)

    # build image
    proc_path = "examples/adapters/proc_example"
    image_name, _, _ = build_processor_image(repo_path, proc_path, use_cache=True)

    # export image
    try:
        docker_export_image(image_name, image_path, keep_image=True)
        assert os.path.isfile(image_path)
    except Exception:
        assert False


def test_cli_builder_cmd(docker_available, node, temp_dir):
    if not docker_available:
        pytest.skip("Docker is not available")

    address = node.rest.address()

    # define arguments
    args = {
        'repository': REPOSITORY_URL,
        'commit_id': REPOSITORY_COMMIT_ID,
        'proc_path': 'examples/adapters/proc_example',
        'address': f"{address[0]}:{address[1]}"
    }

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)
    args['keystore-id'] = keystore.identity.id
    args['keystore'] = temp_dir
    args['password'] = password

    # ensure the node knows about this identity
    node.db.update_identity(keystore.identity)

    try:
        cmd = ProcBuilder()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = node.dor.get_meta(pdi.obj_id)
        assert obj is not None
        assert obj.data_type == 'ProcessorDockerImage'
        assert obj.data_format == 'json'

    except CLIRuntimeError:
        assert False


def test_cli_builder_cmd_store_image(docker_available, node, temp_dir):
    if not docker_available:
        pytest.skip("Docker is not available")

    address = node.rest.address()

    # define arguments
    args = {
        'repository': REPOSITORY_URL,
        'commit_id':  REPOSITORY_COMMIT_ID,
        'proc_path': 'examples/adapters/proc_example',
        'address': f"{address[0]}:{address[1]}",
        'store_image': True
    }

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)
    args['keystore-id'] = keystore.identity.id
    args['keystore'] = temp_dir
    args['password'] = password

    # ensure the node knows about this identity
    node.db.update_identity(keystore.identity)

    try:
        cmd = ProcBuilder()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = node.dor.get_meta(pdi.obj_id)
        assert obj is not None
        assert obj.data_type == 'ProcessorDockerImage'
        assert obj.data_format == 'tar'

    except CLIRuntimeError:
        assert False


def test_cli_rti_proc_deploy_list_show_undeploy(docker_available, node, temp_dir):
    if not docker_available:
        pytest.skip("Docker is not available")

    address = node.rest.address()

    # define arguments
    args = {
        'repository': REPOSITORY_URL,
        'commit_id':  REPOSITORY_COMMIT_ID,
        'proc_path': 'examples/adapters/proc_example',
        'address': f"{address[0]}:{address[1]}",
        'store_image': True
    }

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)
    args['keystore-id'] = keystore.identity.id
    args['keystore'] = temp_dir
    args['password'] = password

    # ensure the node knows about this identity
    node.db.update_identity(keystore.identity)

    try:
        cmd = ProcBuilder()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = node.dor.get_meta(pdi.obj_id)
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
        assert len(result['deployed']) == 0

    except CLIRuntimeError:
        assert False

    # deploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc-id': obj.obj_id,
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
        assert len(result['deployed']) == 1

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
                'proc-id': obj.obj_id
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
            'proc-id': [obj.obj_id]
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
        assert len(result['deployed']) == 0

    except CLIRuntimeError:
        assert False


def test_cli_rti_job_submit_list_status_cancel(docker_available, node, temp_dir):
    if not docker_available:
        pytest.skip("Docker is not available")

    address = node.rest.address()

    # define arguments
    args = {
        'repository': REPOSITORY_URL,
        'commit_id':  REPOSITORY_COMMIT_ID,
        'proc_path': 'examples/adapters/proc_example',
        'address': f"{address[0]}:{address[1]}",
        'store_image': True
    }

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)
    args['keystore-id'] = keystore.identity.id
    args['keystore'] = temp_dir
    args['password'] = password

    # ensure the node knows about this identity
    node.db.update_identity(keystore.identity)

    try:
        cmd = ProcBuilder()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi' in result
        assert result['pdi'] is not None
        pdi: DataObject = result['pdi']

        obj = node.dor.get_meta(pdi.obj_id)
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
            'proc-id': obj.obj_id,
        }

        cmd = RTIProcDeploy()
        result = cmd.execute(args)
        assert result is not None
        assert 'proc' in result
        assert result['proc'] is not None
        # proc: Processor = result['proc']

    except CLIRuntimeError:
        assert False

    # wait for processor to be deployed
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc-id': obj.obj_id,
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
        task = Task(proc_id=proc.id, user_iid=keystore.identity.id, name='test-task', description='',
                    input=[
                        Task.InputValue(name='a', type='value', value={'v': 10}),
                        Task.InputValue(name='b', type='value', value={'v': 10})
                    ],
                    output=[
                        Task.Output(name='c', owner_iid=keystore.identity.id, restricted_access=False,
                                    content_encrypted=False, target_node_iid=node.identity.id)
                    ])
        json.dump(task.dict(), f, indent=2)

    # submit job
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'task': task_path
        }

        cmd = RTIJobSubmit()
        result = cmd.execute(args)
        assert result is not None
        assert 'job' in result
        assert result['job'] is not None
        job = result['job']

    except CLIRuntimeError:
        assert False

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
        assert len(result['jobs']) == 1

    except CLIRuntimeError:
        assert False

    time.sleep(2)

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

    while True:
        # get the status of the job
        try:
            args = {
                'keystore': temp_dir,
                'keystore-id': keystore.identity.id,
                'password': 'password',
                'address': f"{address[0]}:{address[1]}",
                'job-id': job.id
            }

            cmd = RTIJobStatus()
            result = cmd.execute(args)
            assert result is not None
            assert 'status' in result
            assert result['status'] is not None

            status: JobStatus = result['status']
            if status.state == JobStatus.State.CANCELLED:
                break

            elif status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.FAILED]:
                assert False

        except CLIRuntimeError:
            assert False

    time.sleep(1)

    # undeploy the processor
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'proc-id': [obj.obj_id],
            'force': True
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
        assert len(result['deployed']) == 0

    except CLIRuntimeError:
        assert False
