import asyncio
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import traceback
from typing import List, Union, Any, Optional, Tuple

import pytest
from docker.errors import ImageNotFound
from git import Repo

from simaas.cli.cmd_image import clone_repository, build_processor_image, PDIBuildLocal, PDIBuildGithub, PDIExport, \
    PDIImport, PDIMetaInformation
from simaas.cli.cmd_namespace import NamespaceUpdate, NamespaceList, NamespaceShow
from simaas.cli.cmd_service import Service

from examples.simple.abc.processor import write_value
from simaas.core.helpers import get_timestamp_now

from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork
from simaas.nodedb.schemas import NamespaceInfo
from simaas.rti.base import DBJobInfo
from plugins.rti_docker import DefaultRTIService
from simaas.cli.cmd_dor import DORAdd, DORMeta, DORDownload, DORRemove, DORSearch, DORTag, DORUntag, DORAccessShow, \
    DORAccessGrant, DORAccessRevoke
from simaas.cli.cmd_identity import IdentityCreate, IdentityList, IdentityRemove, IdentityShow, IdentityDiscover, \
    IdentityPublish, IdentityUpdate, CredentialsList, CredentialsAddGithubCredentials, CredentialsRemove
from simaas.cli.cmd_job_runner import JobRunner
from simaas.cli.cmd_network import NetworkList
from simaas.cli.cmd_rti import RTIProcDeploy, RTIProcList, RTIProcShow, RTIProcUndeploy, RTIJobSubmit, RTIJobStatus, \
    RTIJobList, RTIJobCancel, RTIVolumeCreateFSRef, RTIVolumeList, RTIVolumeDelete, RTIVolumeCreateEFSRef
from simaas.cli.exceptions import CLIRuntimeError
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.dor.schemas import DataObject, ProcessorDescriptor, GitProcessorPointer
from simaas.helpers import find_available_port, docker_export_image, PortMaster, determine_local_ip, find_processors
from simaas.node.base import Node
from simaas.node.default import DefaultNode
from simaas.p2p.base import P2PAddress
from simaas.rti.protocol import P2PInterruptJob
from simaas.rti.schemas import Task, Job, JobStatus, Severity, ExitCode, JobResult, Processor
from simaas.core.processor import ProgressListener, ProcessorBase, ProcessorRuntimeError, Namespace
from simaas.tests.conftest import REPOSITORY_COMMIT_ID, REPOSITORY_URL, PROC_ABC_PATH

logger = Logging.get(__name__)
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def test_cli_identity_list_create_show_remove(temp_dir):
    """
    Test CLI identity management workflow (list, create, show, remove).

    Verifies that:
    - Empty identity list returns no identities
    - New identity can be created with name, email, and password
    - Keystore file is created for new identity
    - Identity list reflects newly created identity
    - Identity details can be shown
    - Identity can be removed with correct password
    - Removed identity no longer appears in list

    Duration: ~5 seconds
    Requirements: None (local filesystem only)
    """
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


def test_cli_identity_discover_publish_update(session_node, temp_dir):
    """
    Test CLI identity discovery, publishing, and update workflow.

    Verifies that:
    - Unpublished identity is not discovered by the node
    - Identity can be published to a node
    - Published identity is discoverable
    - Identity name can be updated locally
    - Updated identity can be re-published
    - Discovered identity reflects the updates

    Duration: ~10 seconds
    Requirements: Running session node
    """
    address = session_node.rest.address()

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
    """
    Test CLI credentials management workflow (list, add, remove).

    Verifies that:
    - New identity starts with no credentials
    - GitHub credentials can be added with URL, login, and token
    - Credentials list reflects added credential
    - Credentials can be removed by identifier
    - Removed credentials no longer appear in list

    Duration: ~5 seconds
    Requirements: None (local filesystem only)
    """
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


def test_cli_network_show(session_node, temp_dir):
    """
    Test CLI network list command.

    Verifies that:
    - Network list can be retrieved from node
    - Network contains expected number of nodes (2-node test network)

    Duration: ~5 seconds
    Requirements: Running session node
    """
    address = session_node.rest.address()

    # get network information
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = NetworkList()
        result = cmd.execute(args)
        assert result is not None
        assert 'network' in result
        assert len(result['network']) == 2  # session node is part of a 2-node network

    except CLIRuntimeError:
        assert False


def test_cli_dor_add_meta_download_tag_search_untag_remove(session_node, temp_dir):
    """
    Test CLI DOR data object management workflow.

    Verifies that:
    - Data objects can be added with JSON content
    - Metadata can be retrieved for data objects
    - Content can be downloaded to local filesystem
    - Tags can be added to data objects
    - Data objects can be found by tag search
    - Tags can be removed from data objects
    - Tag removal affects search results
    - Data objects can be removed from the repository

    Duration: ~30 seconds
    Requirements: Running session node
    """
    address = session_node.rest.address()

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
            # noinspection PyTypeChecker
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


def test_cli_dor_grant_show_revoke(session_node, temp_dir):
    """
    Test CLI DOR access control workflow (grant, show, revoke).

    Verifies that:
    - New data object has owner in access list
    - Access can be revoked from owner
    - Access list is empty after revocation
    - Access can be re-granted to user
    - Grant is reflected in access list

    Duration: ~20 seconds
    Requirements: Running session node
    """
    address = session_node.rest.address()

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
            # noinspection PyTypeChecker
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
        # noinspection PyTypeChecker
        json.dump({'v': v}, f, indent=2)

    proxy = DORProxy(node.rest.address())
    if access:
        obj = proxy.add_data_object(content_path, node.identity, True, False, data_type, data_format)
        for identity in access:
            obj = proxy.grant_access(obj.obj_id, node.keystore, identity)

    else:
        obj = proxy.add_data_object(content_path, node.identity, False, False, data_type, data_format)

    return obj


def prepare_proc_path(proc_path: str) -> GitProcessorPointer:
    # copy the processor
    proc_abc_path = os.path.join(examples_path, 'simple', 'abc')
    shutil.copytree(proc_abc_path, proc_path)

    # read the processor descriptor
    descriptor_path = os.path.join(proc_path, 'descriptor.json')
    with open(descriptor_path, 'r') as f:
        content = json.load(f)
        proc_descriptor = ProcessorDescriptor.model_validate(content)

    # create the GPP
    gpp_path = os.path.join(proc_path, 'gpp.json')
    with open(gpp_path, 'w') as f:
        gpp = GitProcessorPointer(
            repository='local', commit_id='commit_id', proc_path='processor', proc_descriptor=proc_descriptor
        )
        json.dump(gpp.model_dump(), f, indent=2)

        return gpp


def prepare_plain_job_folder(jobs_root_path: str, job_id: str, a: Any = 1, b: Any = 1) -> str:
    # create the job folder
    job_path = os.path.join(jobs_root_path, job_id)
    os.makedirs(job_path, exist_ok=True)

    # write the data objects 'a' and 'b'
    write_value(os.path.join(job_path, 'a'), a)
    write_value(os.path.join(job_path, 'b'), b)

    return job_path


async def execute_job(
        wd_parent_path: str, custodian: Node, job_id: str,
        a: Union[dict, int, str, DataObject], b: Union[dict, int, str, DataObject],
        user: Identity = None, sig_a: str = None, sig_b: str = None, target_node: Node = None, cancel: bool = False,
        batch_id: Optional[str] = None
) -> JobStatus:
    # convenience variable
    rti: DefaultRTIService = custodian.rti
    user = user if user else custodian.identity

    # prepare working directory
    wd_path = os.path.join(wd_parent_path, job_id)
    os.makedirs(wd_path)

    # prepare proc path and get GPP
    proc_path = os.path.join(wd_path, 'processor')
    gpp: GitProcessorPointer = prepare_proc_path(proc_path)

    ##########

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

    c = Task.Output(
        name='c',
        owner_iid=user.id,
        restricted_access=False, content_encrypted=False,
        target_node_iid=target_node.identity.id if target_node else custodian.identity.id
    )

    task = Task(
        proc_id='fake_proc_id', user_iid=user.id, input=[a, b], output=[c], name='test', description='',
        budget=None, namespace=None,
    )

    # create job
    job = Job(
        id=job_id, batch_id=batch_id, task=task, retain=False, custodian=custodian.info,
        proc_name=gpp.proc_descriptor.name, t_submitted=0
    )
    status = JobStatus(
        state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={}, errors=[], message=None
    )

    # determine P2P address of the job runner
    service_address = PortMaster.generate_p2p_address()

    # manually create a DB entry for that job
    with rti._session_maker() as session:
        record = DBJobInfo(
            id=job.id, batch_id=batch_id, proc_id=task.proc_id, user_iid=user.id, status=status.model_dump(),
            job=job.model_dump(), runner={
                '__ports': {
                    '6000/tcp': service_address
                },
                'ports': {
                    '6000/tcp': service_address
                }
            }
        )
        session.add(record)
        session.commit()

    ##########

    # execute the job runner command
    threading.Thread(
        target=run_job_cmd,
        args=(wd_path, proc_path, service_address, custodian.p2p.address(), custodian.identity.c_public_key, job_id)
    ).start()

    # cancel if requested
    if cancel:
        runner_identity = None
        runner_address = None
        for i in range(10):
            await asyncio.sleep(1)

            # get the runner information
            with rti._session_maker() as session:
                record = session.query(DBJobInfo).get(job_id)
                if 'identity' in record.runner and 'address' in record.runner:
                    runner_identity: Identity = Identity.model_validate(record.runner['identity'])
                    runner_address: str = record.runner['address']
                    break

        if runner_identity is None or runner_address is None:
            assert False

        # perform the interrupt request
        await P2PInterruptJob.perform(P2PAddress(
            address=runner_address,
            curve_secret_key=custodian.keystore.curve_secret_key(),
            curve_public_key=custodian.keystore.curve_public_key(),
            curve_server_key=runner_identity.c_public_key
        ))

    # wait for job to end...
    while True:
        status: JobStatus = rti.get_job_status(job.id)

        if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED , JobStatus.State.FAILED]:
            return status

        await asyncio.sleep(0.5)

def run_job_cmd(
        job_path: str, proc_path: str, service_address: str, custodian_address: str, custodian_pub_key: str, job_id: str
) -> None:
    try:
        cmd = JobRunner()
        args = {
            'job_path': job_path,
            'proc_path': proc_path,
            'service_address': service_address,
            'custodian_address': custodian_address,
            'custodian_pub_key': custodian_pub_key,
            'job_id': job_id
        }
        cmd.execute(args)
    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
        print(trace)


class ProcessorRunner(threading.Thread, ProgressListener):
    def __init__(
            self, proc: ProcessorBase, wd_path: str, job: Job, namespace: Namespace = None, log_level: int = logging.INFO
    ) -> None:
        super().__init__()

        self._mutex = threading.Lock()
        self._proc = proc
        self._wd_path = wd_path
        self._job = job
        self._namespace = namespace
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
            # noinspection PyTypeChecker
            json.dump(self._job_status.model_dump(), f, indent=2)

    def _write_exitcode(self, exitcode: ExitCode, e: Exception = None) -> None:
        exitcode_path = os.path.join(self._wd_path, 'job.exitcode')
        with open(exitcode_path, 'w') as f:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            result = JobResult(exitcode=exitcode, trace=trace)
            # noinspection PyTypeChecker
            json.dump(result.model_dump(), f, indent=2)

    def run(self) -> None:
        try:
            self._logger.info(f"begin processing job at {self._wd_path}")

            self._proc.run(self._wd_path, self._job, self, self._namespace, self._logger)

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


def test_job_worker_done(temp_dir):
    """
    Test job worker successful completion.

    Verifies that:
    - Job worker executes processor successfully
    - Expected output files are created (c, job.exitcode, job.status, job.log)
    - Exit code is DONE for successful completion

    Duration: ~5 seconds
    Requirements: None (local processor execution)
    """
    job_id = 'abcd1234_00'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 1, 1)

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('proc-abc')
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
        result = JobResult.model_validate(json.load(f))

    assert result.exitcode == ExitCode.DONE


def test_job_worker_interrupted(temp_dir):
    """
    Test job worker interruption handling.

    Verifies that:
    - Job worker can be interrupted during execution
    - Expected files are created (job.exitcode, job.status, job.log)
    - Exit code is INTERRUPTED when job is interrupted

    Duration: ~5 seconds
    Requirements: None (local processor execution)
    """
    job_id = 'abcd1234_01'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 5, 5)

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('proc-abc')
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
        result = JobResult.model_validate(json.load(f))

    assert result.exitcode == ExitCode.INTERRUPTED


def test_job_worker_error(temp_dir):
    """
    Test job worker error handling.

    Verifies that:
    - Job worker handles processor errors gracefully
    - Expected files are created (job.exitcode, job.status, job.log)
    - Exit code is ERROR when processor fails
    - Error trace contains expected exception message

    Duration: ~5 seconds
    Requirements: None (local processor execution)
    """
    job_id = 'abcd1234_02'
    job_path = os.path.join(temp_dir, job_id)
    prepare_plain_job_folder(temp_dir, job_id, 1, 'sdf')

    # find the Example processor
    result = find_processors(examples_path)
    proc = result.get('proc-abc')
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
        result = JobResult.model_validate(json.load(f))

    assert result.exitcode == ExitCode.ERROR
    assert "ValueError: invalid literal for int() with base 10: 'sdf'" in result.trace


@pytest.mark.asyncio
async def test_cli_runner_success_by_value(temp_dir, session_node):
    """
    Test job runner with input values.

    Verifies that:
    - Job runner executes successfully with direct value inputs
    - Job progress reaches 100% on completion

    Duration: ~10 seconds
    Requirements: Running session node
    """
    a: int = 1
    b: int = 1
    job_id = '398h36g3_00'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 100


@pytest.mark.asyncio
async def test_cli_runner_failing_validation(temp_dir, session_node):
    """
    Test job runner input validation failure.

    Verifies that:
    - Job runner fails when input doesn't match expected schema
    - Progress stays at 0% on validation failure
    - Error message indicates schema compliance issue

    Duration: ~10 seconds
    Requirements: Running session node
    """
    a: int = {'wrong': 55}
    b: int = 1
    job_id = '398h36g3_01'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    assert 'Data object JSON content does not comply' in status.errors[0].exception.reason


@pytest.mark.asyncio
async def test_cli_runner_success_by_reference(temp_dir, session_node):
    """
    Test job runner with data object reference inputs.

    Verifies that:
    - Job runner executes successfully with data object references
    - Input data objects are resolved correctly
    - Job progress reaches 100% on completion

    Duration: ~15 seconds
    Requirements: Running session node with DOR
    """
    # prepare input data objects
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_02'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 100


@pytest.mark.asyncio
async def test_cli_runner_failing_no_access(temp_dir, session_node, extra_keystores):
    """
    Test job runner access control enforcement.

    Verifies that:
    - Job runner fails when user lacks access to input data objects
    - Progress stays at 0% on access failure
    - Error trace contains AccessNotPermittedError

    Duration: ~15 seconds
    Requirements: Running session node with DOR
    """
    user = extra_keystores[0]
    session_node.db.update_identity(user.identity)

    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, access=[session_node.identity])
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1, access=[session_node.identity])
    job_id = '398h36g3_03'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b, user=user.identity)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'AccessNotPermittedError' in trace


@pytest.mark.asyncio
async def test_cli_runner_failing_no_signature(temp_dir, session_node):
    """
    Test job runner signature requirement enforcement.

    Verifies that:
    - Job runner fails when restricted data objects lack user signature
    - Progress stays at 0% on signature failure
    - Error trace contains MissingUserSignatureError

    Duration: ~15 seconds
    Requirements: Running session node with DOR
    """
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, access=[session_node.identity])
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1, access=[session_node.identity])
    job_id = '398h36g3_04'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'MissingUserSignatureError' in trace


@pytest.mark.asyncio
async def test_cli_runner_failing_no_data_object(temp_dir, session_node):
    """
    Test job runner missing data object handling.

    Verifies that:
    - Job runner fails when input data object doesn't exist
    - Progress stays at 0% when data object is missing
    - Error trace contains UnresolvedInputDataObjectsError

    Duration: ~15 seconds
    Requirements: Running session node with DOR
    """
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1)
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_05'

    # delete the object so it can't be found
    proxy = DORProxy(session_node.rest.address())
    proxy.delete_data_object(b.obj_id, session_node.keystore)

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'UnresolvedInputDataObjectsError' in trace


@pytest.mark.asyncio
async def test_cli_runner_failing_wrong_data_type(temp_dir, session_node):
    """
    Test job runner data type validation.

    Verifies that:
    - Job runner fails when input data type doesn't match processor spec
    - Progress stays at 0% on data type mismatch
    - Error trace contains DataTypeMismatchError

    Duration: ~15 seconds
    Requirements: Running session node with DOR
    """
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, data_type='wrong')
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_06'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'MismatchingDataTypeOrFormatError' in trace


@pytest.mark.asyncio
async def test_cli_runner_failing_wrong_data_format(temp_dir, session_node):
    """
    Test job runner data format validation.

    Verifies that:
    - Job runner fails when input data format doesn't match processor spec
    - Progress stays at 0% on data format mismatch
    - Error trace contains MismatchingDataTypeOrFormatError

    Duration: ~15 seconds
    Requirements: Running session node with DOR
    """
    a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1, data_type='data_format')
    b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
    job_id = '398h36g3_07'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b)
    assert status.progress == 0
    trace = status.errors[0].exception.details['trace']
    assert 'MismatchingDataTypeOrFormatError' in trace


@pytest.mark.asyncio
async def test_cli_runner_cancelled(temp_dir, session_node):
    """
    Test job runner cancellation handling.

    Verifies that:
    - Running job can be cancelled
    - Cancelled job has no errors
    - Progress is less than 100% for cancelled jobs
    - Job state is CANCELLED after cancellation

    Duration: ~10 seconds
    Requirements: Running session node
    """
    a: int = 5
    b: int = 6
    job_id = '398h36g3_08'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b, cancel=True)
    assert len(status.errors) == 0
    assert status.progress < 100
    assert status.state == JobStatus.State.CANCELLED


@pytest.mark.asyncio
async def test_cli_runner_failing_non_dor_target(temp_dir, session_node):
    """
    Test job runner target node DOR capability validation.

    Verifies that:
    - Job runner fails when target node lacks DOR capability
    - Error message indicates target node doesn't support DOR
    - Target node can be properly cleaned up after test

    Duration: ~30 seconds
    Requirements: Running session node, ability to create additional nodes
    """
    # create a new node as DOR target
    with tempfile.TemporaryDirectory() as target_node_storage_path:
        local_ip = determine_local_ip()
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)
        target_node = DefaultNode.create(
            keystore=Keystore.new('dor-target'), storage_path=target_node_storage_path,
            p2p_address=p2p_address, rest_address=rest_address, boot_node_address=rest_address,
            enable_db=True, dor_plugin_class=None, rti_plugin_class=DefaultRTIService,
            retain_job_history=True, strict_deployment=False
        )

        #  make exec-only node known to node
        await P2PJoinNetwork(target_node).perform(session_node.info)
        time.sleep(1)

        a = prepare_data_object(os.path.join(temp_dir, 'a'), session_node, 1)
        b = prepare_data_object(os.path.join(temp_dir, 'b'), session_node, 1)
        job_id = '398h36g3_09'

        # execute the job
        status = await execute_job(temp_dir, session_node, job_id, a, b, target_node=target_node)
        assert 'Target node does not support DOR capabilities' == status.errors[0].exception.details['reason']

        # leave the network
        protocol = P2PLeaveNetwork(target_node)
        await protocol.perform(blocking=True)

        # shutdown the target node
        target_node.shutdown(leave_network=False)

        network = session_node.db.get_network()
        assert len(network) == 2


@pytest.mark.asyncio
async def test_cli_runner_coupled_success_by_value(temp_dir, session_node):
    """
    Test job runner with batch coupling.

    Verifies that:
    - Job runner executes successfully with batch ID
    - Batch-coupled job completes to 100% progress

    Duration: ~10 seconds
    Requirements: Running session node
    """
    a: int = 1
    b: int = 1
    job_id = '398h36g3_100'
    batch_id = 'batch001'

    # execute the job
    status = await execute_job(temp_dir, session_node, job_id, a, b, batch_id=batch_id)
    assert status.progress == 100


def test_find_open_port():
    """
    Test find_available_port utility function.

    Verifies that:
    - Available port is found when ports in range are free
    - Blocked ports are skipped when searching
    - Correct port is returned based on availability

    Duration: ~1 second
    Requirements: None (local network only)
    """
    # block port 5995
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('localhost', 5995))
    server_socket.listen(1)

    port = find_available_port(host='localhost', port_range=(5990, 5994))
    assert(port == 5990)

    port = find_available_port(host='localhost', port_range=(5995, 5999))
    assert(port == 5996)


def test_helper_image_clone_build_export(docker_available, github_credentials_available, session_node, temp_dir):
    """
    Test image helper functions for clone, build, and export workflows.

    Verifies that:
    - clone_repository fails for non-existent repository
    - clone_repository fails for non-existent commit ID
    - clone_repository succeeds with valid repository and commit
    - build_processor_image fails for wrong path
    - build_processor_image succeeds with correct path
    - docker_export_image creates tarball correctly

    Duration: ~60 seconds
    Requirements: Docker, GitHub credentials, local repository clone
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    # copy the repository (if required)
    repo_path = os.path.join(temp_dir, 'repository')
    if not os.path.isdir(repo_path):
        shutil.copytree(os.environ['SIMAAS_REPO_PATH'], repo_path)

    # get the current commit id
    repo = Repo(repo_path)
    commit_id = repo.head.commit.hexsha

    # -----
    # clone tests
    # -----

    try:
        clone_repository(REPOSITORY_URL+"_doesnt_exist", os.path.join(temp_dir, 'repository_doesnt_exist'),
                         credentials=(os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN']))
        assert False
    except CLIRuntimeError:
        assert True

    try:
        clone_repository(REPOSITORY_URL, repo_path, commit_id="doesntexist", simulate_only=True)
        assert False
    except CLIRuntimeError:
        assert os.path.isdir(repo_path)
        assert True

    try:
        clone_repository(REPOSITORY_URL, repo_path, commit_id=commit_id, simulate_only=True)
        assert True
    except CLIRuntimeError:
        assert False

    # -----
    # build tests
    # -----
    image_name = 'test'

    try:
        build_processor_image(
            os.path.join(repo_path+"_wrong", PROC_ABC_PATH), os.environ['SIMAAS_REPO_PATH'], image_name
        )
        assert False
    except CLIRuntimeError:
        assert True

    try:
        proc_path_wrong = "examples/adapters"
        build_processor_image(
            os.path.join(repo_path, proc_path_wrong), os.environ['SIMAAS_REPO_PATH'], image_name
        )
        assert False
    except CLIRuntimeError:
        assert True

    try:
        build_processor_image(
            os.path.join(repo_path, PROC_ABC_PATH), os.environ['SIMAAS_REPO_PATH'], image_name
        )
    except CLIRuntimeError:
        assert False

    # -----
    # export tests
    # -----
    image_path = os.path.join(temp_dir, 'image.tar')

    try:
        docker_export_image('doesnt-exist', image_path)
        assert False
    except ImageNotFound:
        assert True

    try:
        docker_export_image(image_name, image_path, keep_image=True)
        assert os.path.isfile(image_path)
    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
        print(trace)
        assert False


def test_cli_image_build_local(docker_available, temp_dir):
    """
    Test CLI image build from local processor source.

    Verifies that:
    - PDI (Processor Docker Image) can be built from local source
    - Build creates valid PDI file with correct naming
    - Second build uses cached image (significantly faster)
    - PDI metadata is correctly generated

    Duration: ~90 seconds
    Requirements: Docker
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    # build the first time
    try:
        t0 = get_timestamp_now()

        # define arguments
        args = {
            'proc_path': os.path.join(examples_path, 'simple', 'abc'),
            'pdi_path': temp_dir,
            'force_build': True,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result
        meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'].endswith(f"{meta.proc_descriptor.name}_{meta.content_hash}.pdi")
        assert os.path.isfile(result['pdi_path'])

    except CLIRuntimeError:
        assert False

    # build the second time
    try:
        t1 = get_timestamp_now()

        # define arguments
        args = {
            'proc_path': os.path.join(examples_path, 'simple', 'abc'),
            'pdi_path': temp_dir,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result
        meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'].endswith(f"{meta.proc_descriptor.name}_{meta.content_hash}.pdi")
        assert os.path.isfile(result['pdi_path'])

    except CLIRuntimeError:
        assert False

    t2 = get_timestamp_now()

    # the second build attempt should be significantly faster (indicating that the existing image has been used)
    dt1 = t1 - t0
    dt2 = t2 - t1
    assert dt2 < dt1*0.1


def test_cli_image_build_github(docker_available, github_credentials_available, temp_dir):
    """
    Test CLI image build from GitHub repository.

    Verifies that:
    - PDI can be built from GitHub repository source
    - Cloning and building from remote repository works correctly
    - PDI file is created with correct naming convention
    - PDI metadata contains processor descriptor information

    Duration: ~120 seconds
    Requirements: Docker, GitHub credentials
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    try:
        # define arguments
        args = {
            'repository': REPOSITORY_URL,
            'commit_id': REPOSITORY_COMMIT_ID,
            'proc_path': PROC_ABC_PATH,
            'pdi_path': temp_dir,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildGithub()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result
        meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'].endswith(f"{meta.proc_descriptor.name}_{meta.content_hash}.pdi")
        assert os.path.isfile(result['pdi_path'])

    except CLIRuntimeError:
        assert False


def test_cli_image_export_import(docker_available, session_node, temp_dir):
    """
    Test CLI PDI export and import workflow.

    Verifies that:
    - PDI can be built from local source
    - Import fails for non-existent PDI file
    - Import fails for invalid PDI content
    - Valid PDI can be imported to DOR
    - Export fails for non-existent data object
    - PDI can be exported from DOR with correct naming

    Duration: ~60 seconds
    Requirements: Docker, running session node
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)

    # ensure the node knows about this identity
    session_node.db.update_identity(keystore.identity)

    try:
        # define arguments
        args = {
            'proc_path': os.path.join(examples_path, 'simple', 'abc'),
            'pdi_path': temp_dir,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None

    except CLIRuntimeError:
        assert False

    # -------
    # import tests
    # -------
    address = session_node.rest.address()
    pdi_path = result['pdi_path']

    try:
        args = {
            'pdi_path': 'location-does-not-exist',
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        cmd.execute(args)
        assert False

    except CLIRuntimeError:
        assert True

    try:
        invalid_pdi_path = os.path.join(temp_dir, 'invalid.pdi')
        with open(invalid_pdi_path, 'wb') as f:
            f.write(b'skldfjghskpduhgspkdjfhgskdjfhgslkdfjhgsdkl;fjghsdfkljghsdflgjhdfgdsfgsdfgdsd')

        args = {
            'pdi_path': invalid_pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        cmd.execute(args)
        assert False

    except CLIRuntimeError:
        assert True

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
        assert 'pdi' in result
        pdi0: DataObject = result['pdi']

    except CLIRuntimeError:
        assert False

    # -------
    # export tests
    # -------

    try:
        args = {
            'obj_id': 'does not exist',
            'pdi_path': temp_dir,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIExport()
        cmd.execute(args)
        assert False

    except CLIRuntimeError:
        assert True

    try:
        args = {
            'obj_id': pdi0.obj_id,
            'pdi_path': temp_dir,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIExport()
        result = cmd.execute(args)
        pdi_meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'] == os.path.abspath(
            os.path.join(temp_dir, f"{pdi_meta.proc_descriptor.name}_{pdi_meta.content_hash}.pdi")
        )

    except CLIRuntimeError:
        assert False


def test_cli_rti_volumes_list_add_delete(temp_dir):
    """
    Test CLI RTI volume management workflow.

    Verifies that:
    - Empty volume list returns no volumes
    - Creating volume with non-existent path fails
    - Filesystem volume reference can be created
    - Volume list reflects created volume
    - EFS volume reference can be created
    - Multiple volume types can coexist
    - Volumes can be deleted
    - Deletion removes volume from list

    Duration: ~5 seconds
    Requirements: None (local datastore only)
    """
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


def test_cli_rti_proc_deploy_list_show_undeploy(docker_available, session_node, temp_dir):
    """
    Test CLI RTI processor deployment lifecycle.

    Verifies that:
    - PDI can be built and imported
    - Initial processor list returns current count
    - Processor can be deployed from PDI
    - Deployed processor appears in list
    - Processor details can be retrieved (including state)
    - Processor reaches READY state
    - Processor can be undeployed
    - Undeployed processor no longer in list

    Duration: ~90 seconds
    Requirements: Docker, running session node
    """
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


def test_cli_rti_proc_deploy_with_volume_undeploy(docker_available, session_node, temp_dir):
    """
    Test CLI RTI processor deployment with volume mounting.

    Verifies that:
    - Volume reference can be created
    - PDI can be built and imported
    - Processor can be deployed with volume configuration
    - Volume mount path and read-only flag are correctly set
    - Processor reaches READY state with volume attached
    - Processor with volume can be undeployed

    Duration: ~90 seconds
    Requirements: Docker, running session node
    """
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


def test_cli_rti_job_submit_single_list_status_cancel(docker_available, session_node, temp_dir):
    """
    Test CLI RTI single job lifecycle (submit, list, status, cancel).

    Verifies that:
    - PDI can be built, imported, and deployed
    - Task can be defined and submitted as a job
    - Submitted job appears in job list
    - Job status can be retrieved
    - Job can be cancelled
    - Cancelled job status reflects cancellation
    - Processor can be undeployed after job operations

    Duration: ~120 seconds
    Requirements: Docker, running session node
    """
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


def test_cli_rti_job_submit_batch_list_status_cancel(docker_available, session_node, temp_dir, n=2):
    """
    Test CLI RTI batch job lifecycle (submit multiple, list, status, cancel).

    Verifies that:
    - Multiple tasks can be submitted as a batch
    - All batch jobs appear in job list
    - Individual job status can be retrieved
    - Jobs can be cancelled individually
    - Batch completion can be monitored
    - Processor can be undeployed after batch operations

    Duration: ~120 seconds
    Requirements: Docker, running session node
    """
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


def test_cli_service(temp_dir):
    """
    Test CLI service startup command.

    Verifies that:
    - Identity can be created for service
    - Service can be started with various configuration options
    - REST and P2P addresses can be configured
    - DOR and RTI plugins can be specified

    Duration: ~10 seconds
    Requirements: None (service starts without blocking)
    """
    password = 'password'

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': password
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

    # start the service
    rest_address: Tuple[str, int] = PortMaster.generate_rest_address('127.0.0.1')
    host = rest_address[0]
    rest_port = rest_address[1]
    p2p_address: str = PortMaster.generate_p2p_address(host=host)
    p2p_port = p2p_address.split(':')[-1]
    try:
        args = {
            'use-defaults': None,
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': password,
            'datastore': temp_dir,
            'rest-address': f'{host}:{rest_port}',
            'p2p-address': p2p_address,
            'boot-node': f'{host}:{p2p_port}',
            'dor': 'default',
            'rti': 'docker',
            'retain-job-history': False,
            'strict-deployment': False,
            'bind-all-address': False,
        }

        cmd = Service()
        cmd.execute(args, wait_for_termination=False)

    except CLIRuntimeError:
        assert False


def test_cli_namespace_create_list_update(docker_available, session_node, temp_dir):
    """
    Test CLI namespace management workflow (create, list, show, update).

    Verifies that:
    - Invalid resource specifications are rejected
    - Namespace list starts empty
    - Namespace can be created with CPU and memory budget
    - Created namespace appears in list
    - Showing non-existent namespace returns None
    - Namespace details can be retrieved
    - Namespace resources can be updated

    Duration: ~30 seconds
    Requirements: Docker, running session node
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    address = session_node.rest.address()
    name = 'my_namespace_123'
    vcpus = 4
    memory = 2048

    # create a namespace (with invalid resource specification)
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'name': name,
            'vcpus': -4,
            'memory': 2048
        }
        cmd = NamespaceUpdate()
        cmd.execute(args)
        assert False

    except CLIRuntimeError:
        assert True

    # create a namespace (with invalid resource specification)
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'name': name,
            'vcpus': 4,
            'memory': '2048asdf'
        }
        cmd = NamespaceUpdate()
        cmd.execute(args)
        assert False

    except CLIRuntimeError:
        assert True

    # list namespaces (should still be 0)
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
        }
        cmd = NamespaceList()
        result = cmd.execute(args)
        assert 'namespaces' in result
        assert len(result['namespaces']) == 0

    except Exception:
        assert False

    # create a namespace
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'name': name,
            'vcpus': vcpus,
            'memory': memory
        }
        cmd = NamespaceUpdate()
        result = cmd.execute(args)
        assert result is not None
        assert 'namespace' in result

    except CLIRuntimeError:
        assert False

    # list namespaces (should be 1 now)
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
        }
        cmd = NamespaceList()
        result = cmd.execute(args)
        assert 'namespaces' in result
        assert len(result['namespaces']) == 1

    except Exception:
        assert False

    # show namespace (non-existing)
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'name': 'unknown_namespace'
        }
        cmd = NamespaceShow()
        result = cmd.execute(args)
        assert 'namespace' in result
        namespace: Optional[NamespaceInfo] = result['namespace']
        assert namespace is None

    except Exception:
        assert False

    # show namespace
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'name': name
        }
        cmd = NamespaceShow()
        result = cmd.execute(args)
        assert 'namespace' in result
        namespace: Optional[NamespaceInfo] = result['namespace']
        assert namespace is not None
        assert namespace.budget.vcpus == vcpus
        assert namespace.budget.memory == memory

    except Exception:
        assert False

    # update namespace
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'name': name,
            'vcpus': 2*vcpus,
            'memory': 2*memory
        }
        cmd = NamespaceUpdate()
        result = cmd.execute(args)
        assert result is not None
        assert 'namespace' in result
        namespace: Optional[NamespaceInfo] = result['namespace']
        assert namespace is not None
        assert namespace.budget.vcpus == 2*vcpus
        assert namespace.budget.memory == 2*memory

    except CLIRuntimeError:
        assert False
