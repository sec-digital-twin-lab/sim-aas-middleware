"""Integration tests for the P2P networking service."""

import json
import logging
import os
import tempfile
import time
from typing import List, Dict

import pytest

from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger, initialise
from simaas.dor.api import DORProxy
from simaas.core.errors import NetworkError
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject, P2PPushDataObject, P2PRelayPushDataObject
from simaas.dor.schemas import DataObject
from simaas.helpers import PortMaster
from simaas.node.base import Node
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.plugins.builtins.dor_fs import FilesystemDORService
from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork, P2PUpdateIdentity
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.base import P2PAddress
from simaas.core.errors import NetworkError as PeerUnavailableError  # Alias for backwards compat in tests
from simaas.p2p.protocol import P2PLatency, P2PThroughput

initialise(level=logging.DEBUG)
log = get_logger(__name__, 'test')


# ==============================================================================
# Module-level fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def p2p_server(test_context) -> Node:
    """Create a session-scoped P2P server node for networking tests."""
    keystore: Keystore = Keystore.new('p2p_server')
    _node: Node = test_context.get_node(keystore, enable_rest=True, dor_plugin_class=FilesystemDORService, rti_plugin_class=None)
    _node.p2p.add(P2PLatency())
    _node.p2p.add(P2PThroughput())

    yield _node

    _node.shutdown()


@pytest.fixture(scope="session")
def p2p_client(test_context) -> Node:
    """Create a session-scoped P2P client node for networking tests."""
    keystore: Keystore = Keystore.new('p2p_client')
    _node: Node = test_context.get_node(keystore, enable_rest=False, dor_plugin_class=None, rti_plugin_class=None)

    yield _node

    _node.shutdown()


# ==============================================================================
# P2P Tests
# ==============================================================================

@pytest.mark.integration
def test_p2p_latency(p2p_server, p2p_client):
    """Test P2P latency measurement between peers."""
    latency, attempt = P2PLatency.perform(p2p_server.p2p.address(), p2p_server.identity)
    print(f"latency: {latency} msec")
    print(f"attempt: {attempt}")


@pytest.mark.integration
def test_p2p_throughput(p2p_server, p2p_client):
    """Test P2P throughput measurement between peers."""
    upload, download, attempt = P2PThroughput.perform(
        p2p_server.p2p.address(), p2p_server.identity, 100 * 1024 * 1024,
    )
    print(f"upload: {upload:.2f} kB/s")
    print(f"download: {download:.2f} kB/s")
    print(f"attempt: {attempt}")


@pytest.mark.integration
def test_p2p_unreachable(p2p_server, p2p_client):
    """Test handling of unreachable P2P peers."""
    protocol = P2PUpdateIdentity(p2p_client)

    info: NodeInfo = p2p_server.info
    info.p2p_address = PortMaster.generate_p2p_address()

    try:
        protocol.perform(info)
        assert False
    except PeerUnavailableError:
        assert True
    except Exception:
        assert False


@pytest.mark.integration
def test_p2p_update_identity(p2p_server, p2p_client):
    """Test P2P identity update protocol."""
    protocol = P2PUpdateIdentity(p2p_client)

    try:
        result: Identity = protocol.perform(p2p_server.info)
        assert result.id == p2p_server.identity.id
    except Exception:
        assert False


@pytest.mark.integration
def test_p2p_join_leave_network(p2p_server, p2p_client):
    """Test P2P network join and leave operations."""
    networkS: List[NodeInfo] = p2p_server.db.get_network()
    networkC: List[NodeInfo] = p2p_client.db.get_network()
    assert len(networkS) == 1
    assert len(networkC) == 1

    # since we don't know anything about the peer yet, get some info first
    boot_node: NodeInfo = p2p_server.info

    protocol = P2PJoinNetwork(p2p_client)
    result: NodeInfo = protocol.perform(boot_node)
    assert result.identity.id == p2p_server.identity.id

    networkS: List[NodeInfo] = p2p_server.db.get_network()
    networkC: List[NodeInfo] = p2p_client.db.get_network()
    assert len(networkS) == 2
    assert len(networkC) == 2

    protocol = P2PLeaveNetwork(p2p_client)
    protocol.perform(blocking=True)

    networkS: List[NodeInfo] = p2p_server.db.get_network()
    networkC: List[NodeInfo] = p2p_client.db.get_network()
    assert len(networkS) == 1
    assert len(networkC) == 2


@pytest.mark.integration
def test_p2p_lookup_fetch_data_object(p2p_server, p2p_client):
    """Test P2P data object lookup and fetch operations."""
    # client is supposed to be the owner of the data object -> make the server aware of the identity
    owner_ks = p2p_client.keystore
    nodedb = NodeDBProxy(p2p_server.rest.address())
    nodedb.update_identity(owner_ks.identity)

    # upload the data object
    with tempfile.TemporaryDirectory() as temp_dir:
        content_path = os.path.join(temp_dir, 'content.json')
        with open(content_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({'v': 1}, f, indent=2)

        dor = DORProxy(p2p_server.rest.address())
        meta = dor.add_data_object(content_path, owner_ks, False, False, 'JSONObject', 'json')
        obj_id = meta.obj_id

    # perform the lookup
    protocol = P2PLookupDataObject(p2p_client)
    result: Dict[str, DataObject] = protocol.perform(p2p_server.info, [obj_id])
    assert len(result) == 1
    assert obj_id in result

    protocol = P2PFetchDataObject(p2p_client)

    with tempfile.TemporaryDirectory() as temp_dir:
        meta_path = os.path.join(temp_dir, 'meta.json')
        content_path = os.path.join(temp_dir, 'content.json')

        # perform a valid fetch
        try:
            meta: DataObject = protocol.perform(p2p_server.info, obj_id, meta_path, content_path)
            assert meta.obj_id == obj_id
            assert os.path.isfile(meta_path)
            assert os.path.isfile(content_path)
        except Exception:
            assert False

        # perform an invalid fetch
        try:
            protocol.perform(p2p_server.info, '01234', meta_path, content_path)
            assert False
        except NetworkError as e:
            assert 'data object not found' in e.details['reason']
        except Exception:
            assert False


@pytest.mark.integration
def test_p2p_fetch_restricted(p2p_server):
    """Test P2P fetch of restricted data objects with access control."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # create a fresh client node
        keystore = Keystore.new(f"keystore-{get_timestamp_now()}")
        client = DefaultNode(
            keystore, os.path.join(temp_dir, 'client_node'), enable_db=True,
            dor_plugin_class=FilesystemDORService, rti_plugin_class=None
        )
        p2p_address = PortMaster.generate_p2p_address()
        rest_address = PortMaster.generate_rest_address()
        client.startup(p2p_address, rest_address=rest_address)

        # create an owner for the data object -> make the server aware of the identity
        owner = Keystore.new(f"owner-{get_timestamp_now()}")
        nodedb = NodeDBProxy(p2p_server.rest.address())
        nodedb.update_identity(owner.identity)

        # upload the data object
        content_path = os.path.join(temp_dir, 'content.json')
        with open(content_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({'v': 1}, f, indent=2)

        dor = DORProxy(p2p_server.rest.address())
        meta = dor.add_data_object(content_path, owner, True, False, 'JSONObject', 'json')
        obj_id = meta.obj_id

        protocol = P2PFetchDataObject(client)
        meta_path = os.path.join(temp_dir, 'meta.json')
        content_path = os.path.join(temp_dir, 'content.json')

        # try to fetch a data object that doesn't exist
        try:
            fake_obj_id = 'abcdef'
            protocol.perform(p2p_server.info, fake_obj_id, meta_path, content_path)
            assert False
        except NetworkError as e:
            assert 'data object not found' in e.details['reason']
        except Exception:
            assert False

        # the client identity is not known to the server at this point to receive the data object
        try:
            protocol.perform(p2p_server.info, obj_id, meta_path, content_path, user_iid=client.identity.id)
            assert False
        except NetworkError as e:
            assert 'user id not found' in e.details['reason']
        except Exception:
            assert False

        # update the server with the client identity
        p2p_server.db.update_identity(client.identity)

        # the client does not have permission at this point to receive the data object
        try:
            protocol.perform(p2p_server.info, obj_id, meta_path, content_path, user_iid=client.identity.id)
            assert False
        except NetworkError as e:
            assert 'user does not have access' in e.details['reason']
        except Exception:
            assert False

        # grant permission
        dor = DORProxy(p2p_server.rest.address())
        meta = dor.grant_access(obj_id, owner, client.identity)
        assert client.identity.id in meta.access

        # the client does not have a valid permission at this point to receive the data object
        try:
            from simaas.dor.protocol import dor_fetch_token
            invalid_signature = client.keystore.sign(dor_fetch_token(client.identity.id, '12343245'))

            protocol.perform(p2p_server.info, obj_id, meta_path, content_path, user_iid=client.identity.id,
                                   user_signature=invalid_signature)
            assert False
        except NetworkError as e:
            assert 'authorisation failed' in e.details['reason']
        except Exception:
            assert False

        # create valid user signature
        signature = client.keystore.sign(dor_fetch_token(client.identity.id, obj_id))

        # the client does not have permission at this point to receive the data object
        try:
            protocol.perform(p2p_server.info, obj_id, meta_path, content_path,
                                   user_iid=client.identity.id, user_signature=signature)
            assert meta.obj_id == obj_id
            assert os.path.isfile(meta_path)
            assert os.path.isfile(content_path)
        except NetworkError:
            assert False
        except Exception:
            assert False


# ==============================================================================
# P2PRelayPushDataObject — runner pushes via custodian when it cannot reach the
# actual target directly (e.g. cloud function, behind NAT). The custodian, with
# full peer connectivity, performs the downstream push on the runner's behalf.
# ==============================================================================


def _relay_push_kwargs(custodian, runner_keystore, target_iid, content_path):
    return dict(
        custodian_p2p_address=custodian.p2p.address(),
        keystore=runner_keystore,
        custodian_identity=custodian.identity,
        target_iid=target_iid,
        content_path=content_path,
        data_type='JSONObject',
        data_format='json',
        owner_iid=runner_keystore.identity.id,
        creators_iid=[runner_keystore.identity.id],
        access_restricted=False,
        content_encrypted=False,
        license=DataObject.License(by=True, sa=True, nc=True, nd=True),
    )


@pytest.mark.integration
def test_p2p_relay_push_happy_path(p2p_server, p2p_client, test_context):
    """Relay-push from a runner through the custodian to a separate DOR-enabled target node."""
    # spin up a fresh DOR-enabled target and join it to the custodian's network so
    # the custodian's local NodeDB knows where to forward.
    target_keystore = Keystore.new(f"relay-target-happy-{get_timestamp_now()}")
    target_node: Node = test_context.get_node(
        target_keystore, enable_rest=True, dor_plugin_class=FilesystemDORService, rti_plugin_class=None
    )
    P2PJoinNetwork(target_node).perform(p2p_server.info)

    # custodian needs to know about the runner's identity (it stamps owner/creators)
    p2p_server.db.update_identity(p2p_client.identity)

    with tempfile.TemporaryDirectory() as temp_dir:
        content_path = os.path.join(temp_dir, 'payload.json')
        with open(content_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({'v': 42, 'tag': 'happy'}, f)

        meta = P2PRelayPushDataObject.perform(
            **_relay_push_kwargs(p2p_server, p2p_client.keystore, target_node.identity.id, content_path)
        )
        assert meta is not None
        assert meta.obj_id is not None

        # the object should live on the TARGET — the custodian only relayed
        target_meta = target_node.dor.get_meta(meta.obj_id)
        assert target_meta is not None
        assert target_meta.obj_id == meta.obj_id

        custodian_meta = p2p_server.dor.get_meta(meta.obj_id)
        assert custodian_meta is None, "custodian should not retain a copy when relaying"


@pytest.mark.integration
def test_p2p_relay_push_target_unknown(p2p_server, p2p_client):
    """Relay-push with a target_iid the custodian doesn't know about returns a clean error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        content_path = os.path.join(temp_dir, 'payload.json')
        with open(content_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({'v': 1}, f)

        try:
            P2PRelayPushDataObject.perform(
                **_relay_push_kwargs(
                    p2p_server, p2p_client.keystore,
                    target_iid='not-a-real-iid-' + 'x' * 50,
                    content_path=content_path,
                )
            )
            assert False, "expected NetworkError"
        except NetworkError as e:
            assert 'target node not found in network' in e.details['reason']


@pytest.mark.integration
def test_p2p_relay_push_target_no_dor(p2p_server, p2p_client, test_context):
    """Relay-push to a known target that lacks DOR returns a clean error (no 5s timeout)."""
    no_dor_keystore = Keystore.new(f"relay-target-no-dor-{get_timestamp_now()}")
    no_dor_target: Node = test_context.get_node(
        no_dor_keystore, enable_rest=False, dor_plugin_class=None, rti_plugin_class=None
    )
    P2PJoinNetwork(no_dor_target).perform(p2p_server.info)

    with tempfile.TemporaryDirectory() as temp_dir:
        content_path = os.path.join(temp_dir, 'payload.json')
        with open(content_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({'v': 2}, f)

        try:
            P2PRelayPushDataObject.perform(
                **_relay_push_kwargs(
                    p2p_server, p2p_client.keystore, no_dor_target.identity.id, content_path
                )
            )
            assert False, "expected NetworkError"
        except NetworkError as e:
            assert 'does not support DOR' in e.details['reason']


@pytest.mark.integration
def test_p2p_relay_push_target_is_custodian(p2p_server, p2p_client):
    """Relay-push where target == custodian: handler stores via the local DOR.add path."""
    # custodian needs to know about the runner identity
    p2p_server.db.update_identity(p2p_client.identity)

    with tempfile.TemporaryDirectory() as temp_dir:
        content_path = os.path.join(temp_dir, 'payload.json')
        with open(content_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({'v': 99, 'tag': 'self-relay'}, f)

        meta = P2PRelayPushDataObject.perform(
            **_relay_push_kwargs(
                p2p_server, p2p_client.keystore, p2p_server.identity.id, content_path
            )
        )
        assert meta is not None
        assert meta.obj_id is not None

        custodian_meta = p2p_server.dor.get_meta(meta.obj_id)
        assert custodian_meta is not None
        assert custodian_meta.obj_id == meta.obj_id
