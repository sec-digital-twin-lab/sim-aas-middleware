"""Integration tests for the Node Database (NodeDB) service."""

import pytest
import os
import logging
import tempfile
import time

from typing import List, Dict, Optional

from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.helpers import PortMaster
from simaas.node.base import Node
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.plugins.builtins.dor_fs import FilesystemDORService
from simaas.nodedb.exceptions import NodeDBException
from simaas.nodedb.schemas import NodeInfo, ResourceDescriptor

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


# ==============================================================================
# Module-level fixtures
# ==============================================================================

@pytest.fixture(scope="module")
def module_node(test_context, extra_keystores) -> Node:
    """Create a module-scoped node for nodedb testing."""
    _node: Node = test_context.get_node(
        extra_keystores[0], enable_rest=True, dor_plugin_class=FilesystemDORService, rti_plugin_class=None
    )

    yield _node

    _node.shutdown(leave_network=False)


@pytest.fixture(scope="module")
def module_nodedb_proxy(module_node) -> NodeDBProxy:
    """Create a module-scoped NodeDB proxy for testing."""
    proxy = NodeDBProxy(module_node.info.rest_address)
    return proxy


# ==============================================================================
# NodeDB Tests
# ==============================================================================

@pytest.mark.integration
def test_rest_get_node(module_node, module_nodedb_proxy):
    """Test getting node information via REST API."""
    result: NodeInfo = module_nodedb_proxy.get_node()
    assert result is not None
    assert result.identity.id == module_node.identity.id


@pytest.mark.integration
def test_rest_get_network(module_node, module_nodedb_proxy):
    """Test getting network information via REST API."""
    result: List[NodeInfo] = module_nodedb_proxy.get_network()
    assert result is not None
    assert len(result) > 0


@pytest.mark.integration
def test_rest_get_identities(module_node, module_nodedb_proxy):
    """Test getting all identities via REST API."""
    result: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    assert result is not None
    assert len(result) > 0
    assert module_node.identity.id in result


@pytest.mark.integration
def test_rest_get_identity_valid(module_node, module_nodedb_proxy):
    """Test getting a valid identity via REST API."""
    valid_iid = module_node.identity.id

    result: Optional[Identity] = module_nodedb_proxy.get_identity(valid_iid)
    assert result is not None
    assert result.id == valid_iid


@pytest.mark.integration
def test_rest_get_identity_invalid(module_node, module_nodedb_proxy):
    """Test getting an invalid identity via REST API."""
    invalid_iid = 'f00baa'

    result: Optional[Identity] = module_nodedb_proxy.get_identity(invalid_iid)
    assert result is None


@pytest.mark.integration
def test_rest_update_identity_existing(module_node, module_nodedb_proxy):
    """Test updating an existing identity via REST API."""
    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n0 = len(identities)

    name = module_node.identity.name
    new_name = name + "_new"

    keystore = module_node.keystore
    keystore.update_profile(new_name)

    result: Optional[Identity] = module_nodedb_proxy.update_identity(keystore.identity)
    assert result is not None
    assert result.name == new_name

    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n1 = len(identities)
    assert n0 == n1

    keystore.update_profile(name)

    result: Optional[Identity] = module_nodedb_proxy.update_identity(keystore.identity)
    assert result is not None
    assert result.name == name

    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n2 = len(identities)
    assert n0 == n2


@pytest.mark.integration
def test_rest_update_identity_extra(module_node, module_nodedb_proxy):
    """Test adding a new identity via REST API."""
    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n0 = len(identities)

    # create ephemeral keystore and update the nodedb
    keystore = Keystore.new(f'new_keystore_{get_timestamp_now()}')
    module_nodedb_proxy.update_identity(keystore.identity)

    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n1 = len(identities)
    assert n1 == n0 + 1


@pytest.mark.integration
def test_different_address(test_context, module_node, module_nodedb_proxy):
    """Test handling of nodes with conflicting P2P addresses."""
    p2p_address = PortMaster.generate_p2p_address(test_context.host)

    with tempfile.TemporaryDirectory() as tempdir:
        # create two keystores
        keystores = [Keystore.new(f"keystore-{get_timestamp_now()}") for _ in range(2)]

        # determine how many nodes the network has right now, according to the module node
        network: List[NodeInfo] = module_nodedb_proxy.get_network()
        n0 = len(network)

        # manually create a node on a certain address and make it known to the node
        node0 = DefaultNode(
            keystores[0], os.path.join(tempdir, 'node0'), enable_db=True,
            dor_plugin_class=None, rti_plugin_class=None)
        node0.startup(p2p_address, rest_address=None)
        time.sleep(1)

        # at this point node0 should only know about itself
        network: List[NodeInfo] = node0.db.get_network()
        network: List[str] = [item.identity.id for item in network]
        assert len(network) == 1
        assert node0.identity.id in network

        # perform the join
        node0.join_network(module_node.rest.address())
        time.sleep(1)

        # the module node should know of n+1 nodes now
        network: List[NodeInfo] = module_nodedb_proxy.get_network()
        network: List[str] = [item.identity.id for item in network]
        assert len(network) == n0 + 1
        assert module_node.identity.id in network
        assert node0.identity.id in network

        # shutdown the extra node silently (i.e., not leaving the network) - this emulates what happens
        # when a node suddenly crashes for example.
        node0.shutdown(leave_network=False)
        time.sleep(1)

        # the module node should still know n+1 nodes
        network: List[NodeInfo] = module_nodedb_proxy.get_network()
        network: List[str] = [item.identity.id for item in network]
        assert len(network) == n0 + 1
        assert module_node.identity.id in network
        assert node0.identity.id in network

        # manually create another node, using the same address but a different keystore
        node1 = DefaultNode(
            keystores[1], os.path.join(tempdir, 'node1'), enable_db=True,
            dor_plugin_class=None, rti_plugin_class=None
        )
        node1.startup(p2p_address, rest_address=None)
        time.sleep(1)

        # at this point node1 should only know about itself
        network: List[NodeInfo] = node1.db.get_network()
        network: List[str] = [item.identity.id for item in network]
        assert len(network) == 1
        assert module_node.identity.id not in network
        assert node0.identity.id not in network
        assert node1.identity.id in network

        # perform the join
        node1.join_network(module_node.rest.address())
        time.sleep(1)

        # the module node should now still only know of n+1 nodes now (the first node should be replaced)
        network: List[NodeInfo] = module_nodedb_proxy.get_network()
        network: List[str] = [item.identity.id for item in network]
        assert len(network) == n0 + 1
        assert module_node.identity.id in network
        assert node0.identity.id not in network
        assert node1.identity.id in network

        node1.shutdown()
        time.sleep(1)

        # the module node should now only know of n nodes now (the extra node has left)
        network: List[NodeInfo] = module_nodedb_proxy.get_network()
        network: List[str] = [item.identity.id for item in network]
        assert len(network) == n0
        assert module_node.identity.id in network
        assert node0.identity.id not in network
        assert node1.identity.id not in network


@pytest.mark.integration
def test_join_leave_protocol():
    """Test network join and leave protocols."""
    with tempfile.TemporaryDirectory() as tempdir:
        nodes: List[Node] = []
        for i in range(3):
            keystore = Keystore.new(f"keystore-{get_timestamp_now()}")
            node = DefaultNode(
                keystore, os.path.join(tempdir, f'node_{i}'), enable_db=True,
                dor_plugin_class=None, rti_plugin_class=None
            )
            p2p_address = PortMaster.generate_p2p_address()
            rest_address = PortMaster.generate_rest_address()
            node.startup(p2p_address, rest_address=rest_address)
            nodes.append(node)

        time.sleep(1)

        # each node should know about 1 identity: its own
        for node in nodes:
            identities: List[Identity] = node.db.get_identities()
            assert len(identities) == 1
            assert identities[0].id == node.identity.id

        # tell node 1 to join the network with node 0
        nodes[1].join_network(nodes[0].rest.address())
        time.sleep(1)

        # nodes 0 and 1 should know about each other and node 2 only about itself
        for node in nodes:
            identities = node.db.get_identities()
            if node == nodes[0] or node == nodes[1]:
                assert len(identities) == 2
                assert identities[0].id == nodes[0].identity.id or nodes[1].identity.id
                assert identities[1].id == nodes[0].identity.id or nodes[1].identity.id

            else:
                assert len(identities) == 1
                assert identities[0].id == nodes[2].identity.id

        # tell node 2 to join the network with node 0
        nodes[2].join_network(nodes[0].rest.address())
        time.sleep(1)

        # all nodes should now know about each other
        for node in nodes:
            identities = node.db.get_identities()
            assert len(identities) == 3

            network = node.db.get_network()
            assert len(network) == 3

        # tell node 2 to leave the network
        nodes[2].leave_network()
        time.sleep(1)

        # all nodes should still know about each other's identities BUT the network for nodes 0 and 1 is now only
        # of size 2 while node 2 still knows about everyone.
        for node in nodes:
            identities = node.db.get_identities()
            assert len(identities) == 3

            network = node.db.get_network()
            if node == nodes[0] or node == nodes[1]:
                assert len(network) == 2

            else:
                assert len(network) == 3
                assert network[0].identity.id == node.identity.id


@pytest.mark.integration
def test_update_identity():
    """Test identity update propagation across network."""
    with tempfile.TemporaryDirectory() as tempdir:
        nodes: List[Node] = []
        for i in range(3):
            keystore = Keystore.new(f"keystore-{get_timestamp_now()}")
            node = DefaultNode(
                keystore, os.path.join(tempdir, f'node_{i}'), enable_db=True,
                dor_plugin_class=None, rti_plugin_class=None
            )
            p2p_address = PortMaster.generate_p2p_address()
            rest_address = PortMaster.generate_rest_address()
            node.startup(p2p_address, rest_address=rest_address)
            time.sleep(1)

            nodes.append(node)

            if i > 0:
                node.join_network(nodes[0].rest.address())

        # all nodes should now know about each other
        for node in nodes:
            identities = node.db.get_identities()
            assert len(identities) == 3

            network = node.db.get_network()
            assert len(network) == 3

        # get the starting nonce
        node0_id = nodes[0].identity.id
        nonce0_by0_before = nodes[0].db.get_identity(node0_id).nonce
        nonce0_by1_before = nodes[1].db.get_identity(node0_id).nonce
        nonce0_by2_before = nodes[2].db.get_identity(node0_id).nonce

        # update id of node0 but don't propagate
        nodes[0].update_identity(name='bob', propagate=False)
        time.sleep(1)

        nonce0_by0_after = nodes[0].db.get_identity(node0_id).nonce
        nonce0_by1_after = nodes[1].db.get_identity(node0_id).nonce
        nonce0_by2_after = nodes[2].db.get_identity(node0_id).nonce
        assert nonce0_by0_after == nonce0_by0_before + 1
        assert nonce0_by1_before == nonce0_by1_after
        assert nonce0_by2_before == nonce0_by2_after

        # update id of node0
        nodes[0].update_identity(name='jane', propagate=True)
        time.sleep(1)

        nonce0_by0_after = nodes[0].db.get_identity(node0_id).nonce
        nonce0_by1_after = nodes[1].db.get_identity(node0_id).nonce
        nonce0_by2_after = nodes[2].db.get_identity(node0_id).nonce
        assert nonce0_by0_after == nonce0_by0_before + 2
        assert nonce0_by1_after == nonce0_by1_before + 2
        assert nonce0_by2_after == nonce0_by2_before + 2


@pytest.mark.integration
def test_touch_data_object(module_node, module_nodedb_proxy):
    """Test last_seen timestamp updates for identities."""
    # get the identity last seen
    identity: Identity = module_nodedb_proxy.get_identity(module_node.identity.id)
    last_seen = identity.last_seen

    # update the identity
    module_node.update_identity(name='new name')

    # get the identity last seen
    identity: Identity = module_nodedb_proxy.get_identity(module_node.identity.id)
    assert identity.last_seen > last_seen


@pytest.mark.integration
def test_namespace_update(test_context):
    """Test namespace creation and propagation across network."""
    namespace = 'my_namespace'

    # create nodes
    keystores: List[Keystore] = [Keystore.new(f"keystore_{i}", "email") for i in range(3)]
    nodes: List[Node] = [
        test_context.get_node(keystore, enable_rest=True, dor_plugin_class=FilesystemDORService, rti_plugin_class=None)
        for keystore in keystores
    ]

    # initially the namespace doesn't exist -> no nodes knows about it
    for node in nodes:
        assert node.db.get_namespace(namespace) is None

    # update the namespace on node0
    nodes[0].db.update_namespace_budget(namespace, ResourceDescriptor(vcpus=2, memory=2048))

    # node0 should know about it while the others don't
    assert nodes[0].db.get_namespace(namespace) is not None
    for node in nodes[1:]:
        assert node.db.get_namespace(namespace) is None

    # node1-2 joins the network and should then also know about the namespace
    nodes[1].join_network(nodes[0].rest.address())
    nodes[2].join_network(nodes[0].rest.address())
    assert nodes[1].db.get_namespace(namespace) is not None
    assert nodes[2].db.get_namespace(namespace) is not None

    # update the namespace on node1 -> all nodes should know about the new budget
    nodes[1].db.update_namespace_budget(namespace, ResourceDescriptor(vcpus=4, memory=4096))
    for node in nodes:
        ns_info = node.db.get_namespace(namespace)
        assert ns_info is not None
        assert ns_info.budget.vcpus == 4
        assert ns_info.budget.memory == 4096


@pytest.mark.integration
def test_namespace_reserve_cancel(test_context):
    """Test namespace resource reservation and cancellation."""
    namespace = 'my_namespace'
    budget = ResourceDescriptor(vcpus=2, memory=2048)

    # create nodes
    keystores: List[Keystore] = [Keystore.new(f"keystore_{i}", "email") for i in range(3)]
    nodes: List[Node] = [
        test_context.get_node(keystore, enable_rest=True, dor_plugin_class=FilesystemDORService, rti_plugin_class=None)
        for keystore in keystores
    ]

    # make sure nodes have joined the same network
    for node in nodes[1:]:
        node.join_network(nodes[0].rest.address())

    # create the namespace
    nodes[0].db.update_namespace_budget(namespace, budget)

    # all nodes should know about the namespace now
    for node in nodes:
        assert node.db.get_namespace(namespace) is not None

    # make reservation with too much CPUs
    with pytest.raises(NodeDBException) as e:
        nodes[0].db.reserve_namespace_resources(namespace, "job000", ResourceDescriptor(vcpus=4, memory=2048))
    assert f"Resource reservation for {namespace}:job000 failed" in e.value.reason

    # make reservation with too much memory
    with pytest.raises(NodeDBException) as e:
        nodes[0].db.reserve_namespace_resources(namespace, "job001", ResourceDescriptor(vcpus=2, memory=4096))
    assert f"Resource reservation for {namespace}:job001 failed" in e.value.reason

    # make reservation that succeeds
    try:
        nodes[0].db.reserve_namespace_resources(namespace, "job002", ResourceDescriptor(vcpus=2, memory=2048))
    except NodeDBException:
        assert False

    # there should be one reservation
    for node in nodes:
        assert len(node.db.get_namespace(namespace).reservations) == 1

    # cancel reservation that doesn't exist -> there should still be one reservation
    assert nodes[0].db.cancel_namespace_reservation(namespace, "job000") is False
    for node in nodes:
        assert len(node.db.get_namespace(namespace).reservations) == 1

    # cancel reservation that does exist -> there should still be no reservation
    assert nodes[0].db.cancel_namespace_reservation(namespace, "job002") is True
    for node in nodes:
        assert len(node.db.get_namespace(namespace).reservations) == 0
