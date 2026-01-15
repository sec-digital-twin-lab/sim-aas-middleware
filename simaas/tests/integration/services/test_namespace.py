"""Integration tests for the Namespace service.

This module tests namespace functionality including:
- Unknown user handling and authorization
- DOR operations via namespace (add, search, get, remove)
- Access control operations (grant, revoke, transfer ownership)
- RTI operations via namespace (get processors, submit jobs)
"""

import json
import logging
import os
import random
import tempfile
import time
from typing import Optional, List

import pytest

from simaas.rti.schemas import Task, Job, JobStatus, Processor
from simaas.dor.schemas import DataObject, DataObjectProvenance
from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.namespace.default import DefaultNamespace
from simaas.node.base import Node
from plugins.dor_default import DefaultDORService

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


# ==============================================================================
# Module-level fixtures
# ==============================================================================

@pytest.fixture()
def random_content():
    """Create a temporary JSON file with random content for testing.

    Yields:
        Path to a temporary JSON file containing a random integer value.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        content_path = os.path.join(tmpdir, 'test.json')
        with open(content_path, 'w') as f:
            f.write(json.dumps({
                'a': random.randint(0, 9999)
            }))
        yield content_path


@pytest.fixture(scope="session")
def p2p_server(test_context) -> Node:
    """Create a session-scoped P2P server node for namespace testing.

    Creates a node with REST and DOR enabled but without RTI,
    suitable for testing namespace DOR operations.

    Args:
        test_context: The test context providing node creation utilities.

    Yields:
        A configured Node instance.
    """
    keystore: Keystore = Keystore.new('p2p_server')
    _node: Node = test_context.get_node(keystore, enable_rest=True, dor_plugin_class=DefaultDORService, rti_plugin_class=None)

    yield _node

    _node.shutdown(leave_network=False)


@pytest.fixture(scope="session")
def known_user(p2p_server) -> Keystore:
    """Create a keystore for a user known to the p2p_server.

    The user's identity is registered with the server's database,
    allowing them to perform authorized operations.

    Args:
        p2p_server: The P2P server node.

    Returns:
        A Keystore for the known user.
    """
    keystore = Keystore.new('unknown')
    p2p_server.db.update_identity(keystore.identity)
    return keystore


@pytest.fixture(scope="session")
def unknown_user(p2p_server) -> Keystore:
    """Create a keystore for a user unknown to the p2p_server.

    This user's identity is NOT registered with the server,
    so operations requiring authorization will fail.

    Args:
        p2p_server: The P2P server node (unused, but ensures ordering).

    Returns:
        A Keystore for the unknown user.
    """
    return Keystore.new('unknown')


# ==============================================================================
# Namespace Tests
# ==============================================================================

@pytest.mark.integration
def test_namespace_unknown_user(p2p_server, unknown_user):
    """Test that unknown users cannot access namespace services.

    Verifies that:
    - Creating a namespace with an unknown user succeeds
    - Attempting DOR operations with unknown user raises authorization error
    - Error message indicates identity is unknown

    Backend: N/A (DOR only)
    Duration: ~1 second
    Requirements: None
    """
    namespace = DefaultNamespace('test', p2p_server.identity, p2p_server.p2p.address(), unknown_user)

    with pytest.raises(SaaSRuntimeException) as e:
        namespace.dor.statistics()
    assert 'Namespace request authorisation failed: identity unknown' in e.value.reason


@pytest.mark.integration
def test_namespace_dor_add_search_get_remove(p2p_server, known_user, unknown_user, random_content):
    """Test DOR operations through namespace interface.

    Verifies that:
    - Adding data object with unknown owner fails with appropriate error
    - Adding data object with unknown creator fails with appropriate error
    - Adding data object with valid identities succeeds
    - Search returns empty for non-matching criteria
    - Search returns correct results for matching criteria
    - Get metadata returns correct object information
    - Get provenance returns object history
    - Remove successfully deletes data object

    Backend: N/A (DOR only)
    Duration: ~5 seconds
    Requirements: None
    """
    namespace = DefaultNamespace('test', p2p_server.identity, p2p_server.p2p.address(), known_user)

    # unknown owner
    with pytest.raises(SaaSRuntimeException) as e:
        namespace.dor.add(
            random_content, 'JSON', 'json', unknown_user.identity.id, creators_iid=[known_user.identity.id]
        )
    assert 'Identity not found' in e.value.reason

    # unknown creator
    with pytest.raises(SaaSRuntimeException) as e:
        namespace.dor.add(
            random_content, 'JSON', 'json', known_user.identity.id, creators_iid=[unknown_user.identity.id]
        )
    assert 'Identity not found' in e.value.reason

    # successful add
    meta: DataObject = namespace.dor.add(
        random_content, 'JSON', 'json', known_user.identity.id, creators_iid=[known_user.identity.id]
    )
    assert meta is not None

    # successful search
    reply: List[DataObject] = namespace.dor.search(
        data_type='JSON', data_format='sdfsdf'
    )
    assert len(reply) == 0

    # successful search
    reply: List[DataObject] = namespace.dor.search(
        data_type='JSON', data_format='json'
    )
    assert len(reply) == 1

    obj_id = reply[0].obj_id
    c_hash = reply[0].c_hash

    # successful get meta
    meta: Optional[DataObject] = namespace.dor.get_meta(obj_id)
    assert meta is not None
    assert meta.obj_id == obj_id

    # successful get provenance
    result: Optional[DataObjectProvenance] = namespace.dor.get_provenance(c_hash)
    assert result is not None

    result: Optional[DataObjectProvenance] = namespace.dor.get_provenance(c_hash)
    assert result is not None

    result: Optional[DataObject] = namespace.dor.remove(obj_id)
    assert result is not None


@pytest.mark.integration
def test_namespace_dor_access_control(p2p_server, known_user, unknown_user, random_content):
    """Test DOR access control operations through namespace interface.

    Verifies that:
    - Data objects can be added with restricted access
    - Owner can retrieve content of restricted objects
    - Non-owner cannot retrieve content of restricted objects
    - Non-owner cannot grant access to objects they don't own
    - Owner can grant access to other users
    - Users with granted access can retrieve content
    - Owner can revoke access from users
    - Revoked users cannot retrieve content
    - Non-owner cannot transfer ownership
    - Owner can transfer ownership
    - Non-owner (former owner) cannot remove transferred objects
    - New owner can remove objects

    Backend: N/A (DOR only)
    Duration: ~10 seconds
    Requirements: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        other_user = p2p_server.keystore
        namespace0 = DefaultNamespace('test', p2p_server.identity, p2p_server.p2p.address(), known_user)
        namespace1 = DefaultNamespace('test', p2p_server.identity, p2p_server.p2p.address(), other_user)

        # successful add
        meta: DataObject = namespace0.dor.add(
            random_content, 'JSON', 'json', known_user.identity.id, access_restricted=True
        )
        assert meta is not None

        # successful get content
        content_path = os.path.join(temp_dir, f"{meta.obj_id}.content")
        namespace0.dor.get_content(meta.obj_id, content_path)
        assert os.path.isfile(content_path)

        # no access
        with pytest.raises(SaaSRuntimeException) as e:
            namespace1.dor.get_content(meta.obj_id, content_path)
        assert 'user has no access' in e.value.content.details['reason']

        # not owner
        with pytest.raises(SaaSRuntimeException) as e:
            namespace1.dor.grant_access(meta.obj_id, other_user.identity.id)
        assert 'user is not the data object owner' in e.value.content.details['reason']

        # successful grant access
        meta = namespace0.dor.grant_access(meta.obj_id, other_user.identity.id)
        assert other_user.identity.id in meta.access

        # successful get content
        namespace1.dor.get_content(meta.obj_id, content_path)
        assert os.path.isfile(content_path)

        # successful revoke access
        meta = namespace0.dor.revoke_access(meta.obj_id, other_user.identity.id)
        assert other_user.identity.id not in meta.access

        # no access
        with pytest.raises(SaaSRuntimeException) as e:
            namespace1.dor.get_content(meta.obj_id, content_path)
        assert 'user has no access' in e.value.content.details['reason']

        # not owner
        with pytest.raises(SaaSRuntimeException) as e:
            namespace1.dor.transfer_ownership(meta.obj_id, other_user.identity.id)
        assert 'user is not the data object owner' in e.value.content.details['reason']

        # successful transfer ownership
        meta = namespace0.dor.transfer_ownership(meta.obj_id, other_user.identity.id)
        assert meta.owner_iid == other_user.identity.id

        # not owner
        with pytest.raises(SaaSRuntimeException) as e:
            namespace0.dor.remove(meta.obj_id)
        assert 'user is not the data object owner' in e.value.content.details['reason']

        # successful remove
        namespace1.dor.remove(meta.obj_id)


@pytest.mark.integration
def test_namespace_rti_job_procs(
        docker_available, github_credentials_available, session_node, known_user, deployed_abc_processor
):
    """Test RTI processor operations through namespace interface.

    Verifies that:
    - Deployed processors can be listed via namespace
    - Individual processor details can be retrieved via namespace

    Backend: Docker
    Duration: ~5 seconds
    Requirements: Docker, GitHub credentials, deployed ABC processor
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    session_node.db.update_identity(known_user.identity)
    owner = session_node.keystore

    namespace = DefaultNamespace('test', session_node.identity, session_node.p2p.address(), owner)

    procs: List[Processor] = namespace.rti.get_all_procs()
    assert len(procs) > 0

    proc: Optional[Processor] = namespace.rti.get_proc(procs[0].id)
    assert proc is not None


@pytest.mark.integration
def test_namespace_rti_job_submit_status(
        docker_available, github_credentials_available, session_node, known_user, deployed_abc_processor
):
    """Test RTI job submission and status retrieval through namespace interface.

    Verifies that:
    - Jobs can be submitted via namespace
    - Non-owner cannot retrieve job status (authorization check)
    - Owner can retrieve job status
    - Job completes successfully
    - Output data object is created and retrievable
    - Output contains expected computed value

    Backend: Docker
    Duration: ~30 seconds
    Requirements: Docker, GitHub credentials, deployed ABC processor
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    session_node.db.update_identity(known_user.identity)
    proc_id = deployed_abc_processor.obj_id
    wrong_user = known_user
    owner = session_node.keystore

    namespace0 = DefaultNamespace('test', session_node.identity, session_node.p2p.address(), owner)
    namespace1 = DefaultNamespace('test', session_node.identity, session_node.p2p.address(), wrong_user)

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputValue.model_validate({'name': 'a', 'type': 'value', 'value': {'v': 1}}),
            Task.InputValue.model_validate({'name': 'b', 'type': 'value', 'value': {'v': 1}})
        ],
        output=[
            Task.Output.model_validate({'name': 'c', 'owner_iid': owner.identity.id,
                                        'restricted_access': False, 'content_encrypted': False,
                                        'target_node_iid': None})
        ],
        budget=None,
        namespace=None,
        name=None,
        description=None,
    )

    # submit the job
    result = namespace0.rti.submit([task])
    job: Job = result[0]

    # not owner of job
    with pytest.raises(SaaSRuntimeException) as e:
        namespace1.rti.get_job_status(job.id)
    assert 'user is not the job owner or the node owner' in e.value.details['reason']

    while True:
        # get information about the running job
        try:
            status: JobStatus = namespace0.rti.get_job_status(job.id)

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

    with tempfile.TemporaryDirectory() as temp_dir:
        # get the contents of the output data object
        download_path = os.path.join(temp_dir, 'c.json')
        namespace0.dor.get_content(status.output['c'].obj_id, download_path)
        assert os.path.isfile(download_path)

        with open(download_path, 'r') as f:
            content = json.load(f)
            print(content)
            assert (content['v'] == 2)
