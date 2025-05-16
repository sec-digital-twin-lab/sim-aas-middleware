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
from simaas.node.default import DefaultNode, DORType, RTIType
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.protocol import P2PReserveNamespaceResources, P2PCancelNamespaceReservation, \
    P2PClaimNamespaceResources, P2PReleaseNamespaceResources
from simaas.nodedb.schemas import NodeInfo, NamespaceInfo, ResourceDescriptor

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture(scope="module")
def module_node(test_context, extra_keystores) -> Node:
    _node: Node = test_context.get_node(
        extra_keystores[0], enable_rest=True, dor_type=DORType.BASIC, rti_type=DORType.NONE
    )

    yield _node

    _node.shutdown(leave_network=False)


@pytest.fixture(scope="module")
def module_nodedb_proxy(module_node) -> NodeDBProxy:
    proxy = NodeDBProxy(module_node.info.rest_address)
    return proxy


def test_rest_get_node(module_node, module_nodedb_proxy):
    result: NodeInfo = module_nodedb_proxy.get_node()
    assert result is not None
    assert result.identity.id == module_node.identity.id


def test_rest_get_network(module_node, module_nodedb_proxy):
    result: List[NodeInfo] = module_nodedb_proxy.get_network()
    assert result is not None
    assert len(result) > 0


def test_rest_get_identities(module_node, module_nodedb_proxy):
    result: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    assert result is not None
    assert len(result) > 0
    assert module_node.identity.id in result


def test_rest_get_identity_valid(module_node, module_nodedb_proxy):
    valid_iid = module_node.identity.id

    result: Optional[Identity] = module_nodedb_proxy.get_identity(valid_iid)
    assert result is not None
    assert result.id == valid_iid


def test_rest_get_identity_invalid(module_node, module_nodedb_proxy):
    invalid_iid = 'f00baa'

    result: Optional[Identity] = module_nodedb_proxy.get_identity(invalid_iid)
    assert result is None


def test_rest_update_identity_existing(module_node, module_nodedb_proxy):
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


def test_rest_update_identity_extra(module_node, module_nodedb_proxy):
    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n0 = len(identities)

    # create ephemeral keystore and update the nodedb
    keystore = Keystore.new(f'new_keystore_{get_timestamp_now()}')
    module_nodedb_proxy.update_identity(keystore.identity)

    identities: Dict[str, Identity] = module_nodedb_proxy.get_identities()
    n1 = len(identities)
    assert n1 == n0 + 1


def test_different_address(test_context, module_node, module_nodedb_proxy):
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
            dor_type=DORType.NONE, rti_type=RTIType.NONE)
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
            dor_type=DORType.NONE, rti_type=RTIType.NONE
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


def test_join_leave_protocol():
    with tempfile.TemporaryDirectory() as tempdir:
        nodes: List[Node] = []
        for i in range(3):
            keystore = Keystore.new(f"keystore-{get_timestamp_now()}")
            node = DefaultNode(
                keystore, os.path.join(tempdir, f'node_{i}'), enable_db=True,
                dor_type=DORType.NONE, rti_type=RTIType.NONE
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


def test_update_identity():
    with tempfile.TemporaryDirectory() as tempdir:
        nodes: List[Node] = []
        for i in range(3):
            keystore = Keystore.new(f"keystore-{get_timestamp_now()}")
            node = DefaultNode(
                keystore, os.path.join(tempdir, f'node_{i}'), enable_db=True,
                dor_type=DORType.NONE, rti_type=RTIType.NONE
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


def test_touch_data_object(module_node, module_nodedb_proxy):
    # get the identity last seen
    identity: Identity = module_nodedb_proxy.get_identity(module_node.identity.id)
    last_seen = identity.last_seen

    # update the identity
    module_node.update_identity(name='new name')

    # get the identity last seen
    identity: Identity = module_nodedb_proxy.get_identity(module_node.identity.id)
    assert identity.last_seen > last_seen


def test_namespace_get_update(module_node, module_nodedb_proxy):
    name = 'my_namespace'
    budget = ResourceDescriptor(vcpus=2, memory=4096)

    # try to get a namespace that doesn't exist
    namespace: Optional[NamespaceInfo] = module_nodedb_proxy.get_namespace(name)
    assert namespace is None

    # update the namespace (create it)
    namespace: NamespaceInfo = module_nodedb_proxy.update_namespace_budget(name=name, budget=budget)
    assert namespace.name == name
    assert namespace.budget.vcpus == budget.vcpus
    assert namespace.budget.memory == budget.memory

    # try to get a namespace that should exist now
    namespace: Optional[NamespaceInfo] = module_nodedb_proxy.get_namespace(name)
    assert namespace is not None


@pytest.mark.asyncio
async def test_namespace_reserve_cancel(module_node, module_nodedb_proxy):
    name = 'my_namespace2'
    budget = ResourceDescriptor(vcpus=2, memory=4096)

    # update the namespace (create it)
    namespace: NamespaceInfo = module_nodedb_proxy.update_namespace_budget(name=name, budget=budget)
    assert namespace.name == name
    assert namespace.budget.vcpus == budget.vcpus
    assert namespace.budget.memory == budget.memory

    module_node: Node = module_node
    peer = module_node.info

    # make reservation with too much CPUs
    accepted, reason = await P2PReserveNamespaceResources.perform(
        module_node, peer, name, "reservation1", ResourceDescriptor(vcpus=4, memory=4096)
    )
    assert not accepted

    # make reservation with too much memory
    accepted, reason = await P2PReserveNamespaceResources.perform(
        module_node, peer, name, "reservation2", ResourceDescriptor(vcpus=2, memory=8192)
    )
    assert not accepted

    # make reservation that succeeds
    accepted, reason = await P2PReserveNamespaceResources.perform(
        module_node, peer, name, "reservation3", ResourceDescriptor(vcpus=2, memory=4096)
    )
    assert accepted

    # make reservation that fails
    accepted, reason = await P2PReserveNamespaceResources.perform(
        module_node, peer, name, "reservation4", ResourceDescriptor(vcpus=2, memory=4096)
    )
    assert not accepted

    # cancel reservation that doesn't exist
    await P2PCancelNamespaceReservation.perform(
        module_node, peer, name, "reservation55"
    )

    # cancel reservation3
    await P2PCancelNamespaceReservation.perform(
        module_node, peer, name, "reservation3"
    )

    # make reservation that should now succeed
    accepted, reason = await P2PReserveNamespaceResources.perform(
        module_node, peer, name, "reservation4", ResourceDescriptor(vcpus=2, memory=4096)
    )
    assert accepted


@pytest.mark.asyncio
async def test_namespace_reserve_claim_release(module_node, module_nodedb_proxy):
    name = 'my_namespace3'
    budget = ResourceDescriptor(vcpus=2, memory=4096)

    # update the namespace (create it)
    namespace: NamespaceInfo = module_nodedb_proxy.update_namespace_budget(name=name, budget=budget)
    assert namespace.name == name
    assert namespace.budget.vcpus == budget.vcpus
    assert namespace.budget.memory == budget.memory

    module_node: Node = module_node
    peer = module_node.info

    namespace: NamespaceInfo = module_node.db.get_namespace(name)
    print(namespace)
    assert len(namespace.reservations) == 0
    assert len(namespace.claims) == 0
    assert len(namespace.jobs) == 0

    # make reservation
    accepted, reason = await P2PReserveNamespaceResources.perform(
        module_node, peer, name, "reservation", ResourceDescriptor(vcpus=2, memory=4096)
    )
    assert accepted

    namespace: NamespaceInfo = module_node.db.get_namespace(name)
    print(namespace)
    assert len(namespace.reservations) == 1
    assert len(namespace.claims) == 0
    assert len(namespace.jobs) == 0

    # claim a reservation that doesn't exist
    job_id = 'job123'
    accepted, reason = await P2PClaimNamespaceResources.perform(
        module_node, peer, name, "reservation55", job_id
    )
    assert not accepted
    assert reason == f"Reservation 'reservation55' not found in namespace '{name}'"

    namespace: NamespaceInfo = module_node.db.get_namespace(name)
    print(namespace)
    assert len(namespace.reservations) == 1
    assert len(namespace.claims) == 0
    assert len(namespace.jobs) == 0

    # claim the existing reservation
    job_id = 'job123'
    accepted, reason = await P2PClaimNamespaceResources.perform(
        module_node, peer, name, "reservation", job_id
    )
    assert accepted

    namespace: NamespaceInfo = module_node.db.get_namespace(name)
    print(namespace)
    assert len(namespace.reservations) == 0
    assert len(namespace.claims) == 1
    assert len(namespace.jobs) == 1

    # release a claim that doesn't exist
    wrong_job_id = 'job555'
    accepted, reason = await P2PReleaseNamespaceResources.perform(
        module_node, peer, name, wrong_job_id
    )
    assert not accepted
    assert reason == f"Resource claim for job '{wrong_job_id}' not found in namespace '{name}'"

    namespace: NamespaceInfo = module_node.db.get_namespace(name)
    print(namespace)
    assert len(namespace.reservations) == 0
    assert len(namespace.claims) == 1
    assert len(namespace.jobs) == 1

    # release the existing claim that doesn't exist
    accepted, reason = await P2PReleaseNamespaceResources.perform(
        module_node, peer, name, job_id
    )
    assert accepted

    namespace: NamespaceInfo = module_node.db.get_namespace(name)
    print(namespace)
    assert len(namespace.reservations) == 0
    assert len(namespace.claims) == 0
    assert len(namespace.jobs) == 1
