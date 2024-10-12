import os
import logging
import tempfile
import time

import pytest

from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.tests.base_testcase import PortMaster

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture()
def known_users(extra_keystores, node_db_proxy):
    keystores = [extra_keystores[0], extra_keystores[1]]
    for keystore in keystores:
        node_db_proxy.update_identity(keystore.identity)
    return keystores


@pytest.fixture()
def unknown_nodes(test_context, extra_keystores):
    nodes = test_context.create_nodes(extra_keystores, perform_join=False)
    return nodes


@pytest.fixture()
def known_nodes(test_context, extra_keystores):
    nodes = test_context.create_nodes(extra_keystores, perform_join=True)
    return nodes


@pytest.fixture()
def storage_node(test_context, temp_directory):
    keystore = Keystore.new("keystore-storage", "no-email-provided", path=temp_directory, password="password")
    node = test_context.get_node(keystore, use_dor=True, use_rti=False, enable_rest=True)
    yield node


@pytest.fixture()
def execution_node(test_context, temp_directory):
    keystore = Keystore.new("keystore-execution", "no-email-provided", path=temp_directory, password="password")
    node = test_context.get_node(keystore, use_dor=False, use_rti=True, enable_rest=True)
    yield node


@pytest.fixture(scope='function')
def transient_node(test_context, keystore, known_users):
    node = test_context.get_node(keystore, use_dor=True, use_rti=True, enable_rest=True)
    for keystore in known_users:
        node.db.update_identity(keystore.identity)
    return node


def test_rest_get_node(node_db_proxy):
    node = node_db_proxy.get_node()
    assert (node is not None)


def test_rest_get_network(node_db_proxy):
    network = node_db_proxy.get_network()
    assert (network is not None)
    assert (len(network) > 0)


def test_rest_get_identities(node_db_proxy, known_users):
    identities = node_db_proxy.get_identities()
    assert (identities is not None)
    assert (len(identities) > 0)


def test_rest_get_identity_valid(node, node_db_proxy):
    valid_iid = node.identity.id

    identity = node_db_proxy.get_identity(valid_iid)
    assert (identity is not None)
    assert (identity.id == node.identity.id)


def test_rest_get_identity_invalid(node_db_proxy):
    invalid_iid = 'f00baa'

    identity = node_db_proxy.get_identity(invalid_iid)
    assert (identity is None)


def test_rest_update_identity_existing(node, node_db_proxy):
    name = node.identity.name
    new_name = name + "2"

    keystore = node.keystore
    keystore.update_profile(new_name)

    result = node_db_proxy.update_identity(keystore.identity)
    assert (result is not None)
    assert (result.name == new_name)

    # revert name
    keystore.update_profile(name)


def test_rest_update_identity_extra(test_context, node_db_proxy):
    identities0 = node_db_proxy.get_identities()

    extra = test_context.create_keystores(1)[0]
    node_db_proxy.update_identity(extra.identity)

    identities1 = node_db_proxy.get_identities()
    assert (len(identities1) == len(identities0) + 1)


def test_node_self_awareness(transient_node, keystore, known_users):
    identities = transient_node.db.get_identities()
    assert (len(identities) == 1 + len(known_users))
    identities = {i.id: i for i in identities}
    print(identities)
    assert (identities[transient_node.identity.id].name == keystore.identity.name)

    network = transient_node.db.get_network()
    assert (len(network) == 1)
    assert (network[0].identity.id == transient_node.identity.id)


def test_different_address(test_context, node, node_db_proxy):
    p2p_address = PortMaster.generate_p2p_address(test_context.host)

    with tempfile.TemporaryDirectory() as tempdir:
        # create two keystores
        keystores = [Keystore.new(f"keystore-{i}", "no-email-provided", path=tempdir, password="password") for i in range(2)]

        # determine how many nodes the network has right now, according to the node
        network = node_db_proxy.get_network()
        network = [item.identity.id for item in network]
        n_nodes = len(network)

        # manually create a node on a certain address and make it known to the node
        node0 = DefaultNode(keystores[0], os.path.join(test_context.testing_dir, 'node0'),
                            enable_db=True, enable_dor=False, enable_rti=False)
        node0.startup(p2p_address, rest_address=None)

        # at this point node0 should only know about itself
        network = node0.db.get_network()
        network = [item.identity.id for item in network]
        assert (len(network) == 1)
        assert (node0.identity.id in network)

        # perform the join
        node0.join_network(node.p2p.address())

        # the node should know of n+1 nodes now
        network = node_db_proxy.get_network()
        network = [item.identity.id for item in network]
        assert (len(network) == n_nodes + 1)
        assert (node.identity.id in network)
        assert (node0.identity.id in network)

        # shutdown the first node silently (i.e., not leaving the network) - this emulates what happens
        # when a node suddenly crashes for example.
        node0.shutdown(leave_network=False)

        # the node should still know n+1 nodes
        network = node_db_proxy.get_network()
        network = [item.identity.id for item in network]
        assert (len(network) == n_nodes + 1)
        assert (node.identity.id in network)
        assert (node0.identity.id in network)

        # manually create another node, using the same address but a different keystore
        node1 = DefaultNode(keystores[1], os.path.join(test_context.testing_dir, 'node1'),
                            enable_db=True, enable_dor=False, enable_rti=False)
        node1.startup(p2p_address, rest_address=None)

        # at this point node1 should only know about itself
        network = node1.db.get_network()
        network = [item.identity.id for item in network]
        assert (len(network) == 1)
        assert (node1.identity.id in network)

        # perform the join
        node1.join_network(node.p2p.address())

        # the node should now still only know of n+1 nodes now (the first node should be replaced)
        network = node_db_proxy.get_network()
        network = [item.identity.id for item in network]
        assert (len(network) == n_nodes + 1)
        assert (node.identity.id in network)
        assert (node1.identity.id in network)
        assert (node0.identity.id not in network)

        node1.shutdown()


def test_join_leave_protocol(unknown_nodes):
    nodes = unknown_nodes
    # each node should know about 1 identity: its own
    for node in nodes:
        identities = node.db.get_identities()
        assert (len(identities) == 1)
        assert (identities[0].id == node.identity.id)

    # tell node 1 to join the network with node 0
    nodes[1].join_network(nodes[0].p2p.address())

    # nodes 0 and 1 should know about each other and node 2 only about itself
    for node in nodes:
        identities = node.db.get_identities()
        if node == nodes[0] or node == nodes[1]:
            assert (len(identities) == 2)
            assert (identities[0].id == nodes[0].identity.id or nodes[1].identity.id)
            assert (identities[1].id == nodes[0].identity.id or nodes[1].identity.id)

        else:
            assert (len(identities) == 1)
            assert (identities[0].id == nodes[2].identity.id)

    # tell node 2 to join the network with node 0
    nodes[2].join_network(nodes[0].p2p.address())

    # all nodes should now know about each other
    for node in nodes:
        identities = node.db.get_identities()
        assert (len(identities) == 3)

        network = node.db.get_network()
        assert (len(network) == 3)

    # tell node 2 to leave the network
    nodes[2].leave_network()

    # all nodes should still know about each other's identities BUT the network for nodes 0 and 1 is now only
    # of size 2 while node 2 now only knows about itself.
    for node in nodes:
        identities = node.db.get_identities()
        assert (len(identities) == 3)

        network = node.db.get_network()
        if node == nodes[0] or node == nodes[1]:
            assert (len(network) == 2)

        else:
            assert (len(network) == 1)
            assert (network[0].identity.id == node.identity.id)


def test_update_identity(known_nodes):
    nodes = known_nodes
    # all nodes should now know about each other
    for node in nodes:
        identities = node.db.get_identities()
        assert (len(identities) == 3)

        network = node.db.get_network()
        assert (len(network) == 3)

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
    assert (nonce0_by0_after == nonce0_by0_before + 1)
    assert (nonce0_by1_before == nonce0_by1_after)
    assert (nonce0_by2_before == nonce0_by2_after)

    # update id of node0
    nodes[0].update_identity(name='jane', propagate=True)
    time.sleep(1)

    nonce0_by0_after = nodes[0].db.get_identity(node0_id).nonce
    nonce0_by1_after = nodes[1].db.get_identity(node0_id).nonce
    nonce0_by2_after = nodes[2].db.get_identity(node0_id).nonce
    assert (nonce0_by0_after == nonce0_by0_before + 2)
    assert (nonce0_by1_after == nonce0_by1_before + 2)
    assert (nonce0_by2_after == nonce0_by2_before + 2)


def test_proxy(test_context, temp_directory):
    keystores = []
    for i in range(3):
        keystore = Keystore.new(f"keystore-test_proxy-{i}", "no-email-provided", path=temp_directory, password="password")
        keystores.append(keystore)

    nodes = test_context.create_nodes(keystores, perform_join=True, enable_rest=True)
    time.sleep(2)

    iid0 = nodes[0].identity.id

    proxy0 = NodeDBProxy(nodes[0].rest.address())
    proxy1 = NodeDBProxy(nodes[1].rest.address())
    proxy2 = NodeDBProxy(nodes[2].rest.address())

    result = proxy0.get_node()
    assert (result is not None)
    assert (result.identity.id == iid0)

    result = proxy0.get_network()
    assert (result is not None)
    assert (len(result) == 3)

    result = proxy0.get_identities()
    assert (result is not None)
    assert (len(result) == 3)

    identity = proxy0.get_identity(iid0)
    assert (identity.id == iid0)

    identity = nodes[0].update_identity(name='updated_name')
    proxy0.update_identity(identity)

    result = proxy0.get_identities()
    assert (result[iid0].name == 'updated_name')

    result = proxy1.get_identities()
    assert (result[iid0].name == 'updated_name')

    result = proxy2.get_identities()
    assert (result[iid0].name == 'updated_name')


def test_service_availability(storage_node, execution_node):
    node_s = storage_node
    node_e = execution_node

    proxy_s = NodeDBProxy(node_s.rest.address())
    proxy_e = NodeDBProxy(node_e.rest.address())

    result_s = proxy_s.get_node()
    assert (result_s.dor_service is True)
    assert (result_s.rti_service is False)

    result_e = proxy_e.get_node()
    assert (result_e.dor_service is False)
    assert (result_e.rti_service is True)


def test_touch_data_object(node_db_proxy, node):
    # get the identity last seen
    identity: Identity = node_db_proxy.get_identity(node.identity.id)
    last_seen = identity.last_seen

    # update the identity
    node.update_identity(name='new name')

    # get the identity last seen
    identity: Identity = node_db_proxy.get_identity(node.identity.id)
    assert (identity.last_seen > last_seen)
