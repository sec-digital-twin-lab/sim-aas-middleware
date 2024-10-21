import asyncio
import json
import logging
import os
import tempfile
from typing import List, Dict

import pytest

from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.dor.exceptions import FetchDataObjectFailedError
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject
from simaas.dor.schemas import DataObject
from simaas.helpers import PortMaster
from simaas.node.base import Node
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork, P2PUpdateIdentity
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.exceptions import PeerUnavailableError

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture(scope="module")
def p2p_server(test_context, extra_keystores) -> Node:
    _node: Node = test_context.get_node(extra_keystores[0], enable_rest=True, use_dor=True, use_rti=False)

    yield _node

    _node.shutdown(leave_network=False)


@pytest.fixture(scope="module")
def p2p_client(test_context, extra_keystores) -> Node:
    _node: Node = test_context.get_node(extra_keystores[1], enable_rest=False, use_dor=False, use_rti=False)

    yield _node

    _node.shutdown(leave_network=False)


@pytest.mark.asyncio
async def test_p2p_unreachable(p2p_server, p2p_client):
    protocol = P2PUpdateIdentity(p2p_client)

    info: NodeInfo = p2p_server.info
    info.p2p_address = PortMaster.generate_p2p_address()

    try:
        await protocol.perform(info)
        assert False
    except PeerUnavailableError:
        assert True
    except Exception:
        assert False


@pytest.mark.asyncio
async def test_p2p_update_identity(p2p_server, p2p_client):
    protocol = P2PUpdateIdentity(p2p_client)

    try:
        result: Identity = await protocol.perform(p2p_server.info)
        assert result.id == p2p_server.identity.id
    except Exception:
        assert False


@pytest.mark.asyncio
async def test_p2p_join_leave_network(p2p_server, p2p_client):
    networkS: List[NodeInfo] = p2p_server.db.get_network()
    networkC: List[NodeInfo] = p2p_client.db.get_network()
    assert len(networkS) == 1
    assert len(networkC) == 1

    # since we don't know anything about the peer yet, get some info first
    boot_node: NodeInfo = p2p_server.info

    protocol = P2PJoinNetwork(p2p_client)
    result: NodeInfo = await protocol.perform(boot_node)
    assert result.identity.id == p2p_server.identity.id

    networkS: List[NodeInfo] = p2p_server.db.get_network()
    networkC: List[NodeInfo] = p2p_client.db.get_network()
    assert len(networkS) == 2
    assert len(networkC) == 2

    protocol = P2PLeaveNetwork(p2p_client)
    await protocol.perform(blocking=True)

    networkS: List[NodeInfo] = p2p_server.db.get_network()
    networkC: List[NodeInfo] = p2p_client.db.get_network()
    assert len(networkS) == 1
    assert len(networkC) == 2


@pytest.mark.asyncio
async def test_p2p_lookup_fetch_data_object(p2p_server, p2p_client):
    # client is supposed to be the owner of the data object -> make the server aware of the identity
    owner = p2p_client.identity
    nodedb = NodeDBProxy(p2p_server.rest.address())
    nodedb.update_identity(owner)

    # upload the data object
    with tempfile.TemporaryDirectory() as temp_dir:
        content_path = os.path.join(temp_dir, 'content.json')
        with open(content_path, 'w') as f:
            json.dump({'v': 1}, f, indent=2)

        dor = DORProxy(p2p_server.rest.address())
        meta = dor.add_data_object(content_path, owner, False, False, 'JSONObject', 'json')
        obj_id = meta.obj_id

    # perform the lookup
    protocol = P2PLookupDataObject(p2p_client)
    result: Dict[str, DataObject] = await protocol.perform(p2p_server.info, [obj_id])
    assert len(result) == 1
    assert obj_id in result

    protocol = P2PFetchDataObject(p2p_client)

    with tempfile.TemporaryDirectory() as temp_dir:
        meta_path = os.path.join(temp_dir, 'meta.json')
        content_path = os.path.join(temp_dir, 'content.json')

        # perform a valid fetch
        try:
            meta: DataObject = await protocol.perform(p2p_server.info, obj_id, meta_path, content_path)
            assert meta.obj_id == obj_id
            assert os.path.isfile(meta_path)
            assert os.path.isfile(content_path)
        except Exception:
            assert False

        # perform an invalid fetch
        try:
            await protocol.perform(p2p_server.info, '01234', meta_path, content_path)
            assert False
        except FetchDataObjectFailedError as e:
            assert 'data object not found' in e.details['reason']
        except Exception:
            assert False


@pytest.mark.asyncio
async def test_p2p_lookup_fetch_data_object_restricted(p2p_server):
    with tempfile.TemporaryDirectory() as temp_dir:
        # create a fresh client node
        keystore = Keystore.new(f"keystore-{get_timestamp_now()}")
        client = DefaultNode(keystore, os.path.join(temp_dir, f'client_node'),
                             enable_db=True, enable_dor=False, enable_rti=False)
        p2p_address = PortMaster.generate_p2p_address()
        rest_address = PortMaster.generate_rest_address()
        client.startup(p2p_address, rest_address=rest_address)
        await asyncio.sleep(1)

        # create an owner for the data object -> make the server aware of the identity
        owner = Keystore.new(f"owner-{get_timestamp_now()}")
        nodedb = NodeDBProxy(p2p_server.rest.address())
        nodedb.update_identity(owner.identity)

        # upload the data object
        content_path = os.path.join(temp_dir, 'content.json')
        with open(content_path, 'w') as f:
            json.dump({'v': 1}, f, indent=2)

        dor = DORProxy(p2p_server.rest.address())
        meta = dor.add_data_object(content_path, owner.identity, True, False, 'JSONObject', 'json')
        obj_id = meta.obj_id

        protocol = P2PFetchDataObject(client)
        meta_path = os.path.join(temp_dir, 'meta.json')
        content_path = os.path.join(temp_dir, 'content.json')

        # try to fetch a data object that doesn't exist
        try:
            fake_obj_id = 'abcdef'
            await protocol.perform(p2p_server.info, fake_obj_id, meta_path, content_path)
            assert False
        except FetchDataObjectFailedError as e:
            assert 'data object not found' in e.details['reason']
        except Exception:
            assert False

        # the client identity is not known to the server at this point to receive the data object
        try:
            await protocol.perform(p2p_server.info, obj_id, meta_path, content_path, user_iid=client.identity.id)
            assert False
        except FetchDataObjectFailedError as e:
            assert 'user id not found' in e.details['reason']
        except Exception:
            assert False

        # update the server with the client identity
        p2p_server.db.update_identity(client.identity)

        # the client does not have permission at this point to receive the data object
        try:
            await protocol.perform(p2p_server.info, obj_id, meta_path, content_path, user_iid=client.identity.id)
            assert False
        except FetchDataObjectFailedError as e:
            assert 'user does not have access' in e.details['reason']
        except Exception:
            assert False

        # grant permission
        dor = DORProxy(p2p_server.rest.address())
        meta = dor.grant_access(obj_id, owner, client.identity)
        assert client.identity.id in meta.access

        # the client does not have a valid permission at this point to receive the data object
        try:
            token = f"{client.identity.id}:12343245"
            invalid_signature = client.keystore.sign(token.encode('utf-8'))

            await protocol.perform(p2p_server.info, obj_id, meta_path, content_path, user_iid=client.identity.id,
                                   user_signature=invalid_signature)
            assert False
        except FetchDataObjectFailedError as e:
            assert 'authorisation failed' in e.details['reason']
        except Exception:
            assert False

        # create valid user signature
        token = f"{client.identity.id}:{obj_id}"
        signature = client.keystore.sign(token.encode('utf-8'))

        # the client does not have permission at this point to receive the data object
        try:
            await protocol.perform(p2p_server.info, obj_id, meta_path, content_path,
                                   user_iid=client.identity.id, user_signature=signature)
            assert meta.obj_id == obj_id
            assert os.path.isfile(meta_path)
            assert os.path.isfile(content_path)
        except FetchDataObjectFailedError:
            assert False
        except Exception:
            assert False
