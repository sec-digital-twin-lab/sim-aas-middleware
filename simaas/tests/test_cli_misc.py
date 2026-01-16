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
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir



def test_cli_service(temp_dir):
    """Test CLI service startup command."""
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
    """Test CLI namespace management workflow (create, list, show, update)."""
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
