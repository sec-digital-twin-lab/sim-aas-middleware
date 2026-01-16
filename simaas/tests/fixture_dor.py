"""DOR (Data Object Repository) test fixtures.

This module contains:
- session_node fixture (with DOR enabled)
- dor_proxy fixture
- node_db_proxy fixture
- session_data_dir fixture
"""

import os
import tempfile

import pytest

from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.helpers import determine_local_ip, PortMaster
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy

# Import plugin classes
from plugins.dor_default import DefaultDORService
from plugins.rti_docker import DefaultRTIService

logger = Logging.get('tests.fixtures.dor')


@pytest.fixture(scope="session")
def session_node(session_keystore):
    """Session-scoped node with DOR and RTI services enabled.

    Creates a two-node network for testing network behavior.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        local_ip = determine_local_ip()

        # create node0
        datastore0 = os.path.join(tempdir, 'session_node0')
        rest_address0 = PortMaster.generate_rest_address(host=local_ip)
        p2p_address0 = PortMaster.generate_p2p_address(host=local_ip)
        _node0 = DefaultNode.create(
            keystore=session_keystore, storage_path=datastore0,
            p2p_address=p2p_address0, rest_address=rest_address0, boot_node_address=rest_address0,
            enable_db=True, dor_plugin_class=DefaultDORService, rti_plugin_class=DefaultRTIService,
            retain_job_history=False, strict_deployment=False
        )

        # create node1 to ensure there is a network (of 2 nodes). the rationale here is that some errors may only
        # occur if there is a network of nodes. so even though we only return one node as 'session node' that node
        # is part of a network.
        keystore1 = Keystore.new('session_node1')
        datastore1 = os.path.join(tempdir, 'session_node1')
        rest_address1 = PortMaster.generate_rest_address(host=local_ip)
        p2p_address1 = PortMaster.generate_p2p_address(host=local_ip)
        _node1 = DefaultNode.create(
            keystore=keystore1, storage_path=datastore1,
            p2p_address=p2p_address1, rest_address=rest_address1, boot_node_address=rest_address0,
            enable_db=True, dor_plugin_class=DefaultDORService, rti_plugin_class=None,
            retain_job_history=False, strict_deployment=False
        )

        network = _node0.db.get_network()
        assert len(network) == 2

        yield _node0

        _node0.shutdown(leave_network=False)
        _node1.shutdown(leave_network=False)


@pytest.fixture(scope="session")
def session_data_dir(session_keystore):
    """Session-scoped temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


@pytest.fixture(scope="session")
def dor_proxy(session_node):
    """Session-scoped DOR proxy connected to session_node."""
    proxy = DORProxy(session_node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def node_db_proxy(session_node):
    """Session-scoped NodeDB proxy connected to session_node."""
    proxy = NodeDBProxy(session_node.rest.address())
    return proxy
