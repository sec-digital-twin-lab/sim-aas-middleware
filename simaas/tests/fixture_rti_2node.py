"""RTI 2-Node test fixtures for P2P testing.

This module provides fixtures for testing RTI with a 2-node setup where:
- Storage Node: DOR enabled, RTI disabled (--dor fs --rti none)
- Execution Node: DOR disabled, RTI enabled (--dor none --rti docker)

This forces all data to flow through P2P, exposing issues single-node tests cannot detect.
"""

import os
import tempfile
from dataclasses import dataclass

import pytest

from simaas.core.async_helpers import run_coro_safely
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.dor.api import DORProxy
from simaas.dor.schemas import DataObject
from simaas.helpers import determine_local_ip, PortMaster
from simaas.node.default import DefaultNode
from simaas.nodedb.api import NodeDBProxy
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import ProcessorVolume

# Import plugin classes
from simaas.plugins.builtins.dor_fs import FilesystemDORService
from simaas.plugins.builtins.rti_docker import DockerRTIService

from simaas.tests.fixture_rti import (
    add_test_processor,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
    PROC_ABC_PATH,
    PROC_ROOM_PATH,
    PROC_THERMOSTAT_PATH,
)

log = get_logger('tests.fixtures.rti_2node', 'test')


@dataclass
class RTI2NodeContext:
    """Context for 2-node RTI tests.

    Provides all dependencies needed to run RTI tests against a 2-node setup
    where storage and execution are separated to force P2P data flow.
    """
    storage_node: DefaultNode
    execution_node: DefaultNode
    storage_dor_proxy: DORProxy
    execution_rti_proxy: RTIProxy
    storage_nodedb_proxy: NodeDBProxy
    execution_nodedb_proxy: NodeDBProxy
    deployed_abc_processor: DataObject
    deployed_room_processor: DataObject
    deployed_thermostat_processor: DataObject
    default_memory: int = 1024

    def get_known_user(self, extra_keystores) -> Keystore:
        """Register and return a known user for authorization tests.

        In 2-node setup, user identities must be registered on BOTH nodes.
        """
        keystore = extra_keystores[2]
        self.storage_nodedb_proxy.update_identity(keystore.identity)
        self.execution_nodedb_proxy.update_identity(keystore.identity)
        return keystore


class ProcessorDeployment2Node:
    """Context manager for deploying processors in 2-node setup.

    In 2-node setup:
    - PDI is added to storage node's DOR
    - PDI is deployed to execution node's RTI
    - This triggers P2P fetch of processor image from storage to execution
    """

    def __init__(
        self,
        proc_name: str,
        proc_path: str,
        storage_dor_proxy: DORProxy,
        execution_rti_proxy: RTIProxy,
        storage_keystore: Keystore,
        execution_keystore: Keystore,
        docker_available: bool,
        volumes: list = None,
        platform: str = 'linux/amd64'
    ):
        self.proc_name = proc_name
        self.proc_path = proc_path
        self.storage_dor_proxy = storage_dor_proxy
        self.execution_rti_proxy = execution_rti_proxy
        self.storage_keystore = storage_keystore
        self.execution_keystore = execution_keystore
        self.docker_available = docker_available
        self.volumes = volumes
        self.platform = platform
        self.meta: DataObject = None
        self.proc_id: str = None

    def __enter__(self) -> DataObject:
        """Deploy processor: add to storage DOR, deploy to execution RTI."""
        # Add to storage node's DOR
        self.meta = add_test_processor(
            self.storage_dor_proxy, self.storage_keystore,
            proc_name=self.proc_name, proc_path=self.proc_path, platform=self.platform
        )
        self.proc_id = self.meta.obj_id

        if not self.docker_available:
            return self.meta

        # Deploy to execution node's RTI (will P2P fetch from storage)
        self.execution_rti_proxy.deploy(self.proc_id, self.execution_keystore, volumes=self.volumes)
        wait_for_processor_ready(self.execution_rti_proxy, self.proc_id)
        log.info(f"Processor deployed to execution node: {self.proc_id}")

        return self.meta

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Undeploy processor from execution node."""
        if not self.docker_available or self.proc_id is None:
            return

        self.execution_rti_proxy.undeploy(self.proc_id, self.execution_keystore)
        wait_for_processor_undeployed(self.execution_rti_proxy, self.proc_id)
        log.info(f"Processor undeployed from execution node: {self.proc_id}")


# ==============================================================================
# 2-Node Keystore Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def session_keystore_storage():
    """Session-scoped keystore for the storage node."""
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("storage_node", "storage@test.local", path=tempdir, password="password")
        yield keystore


@pytest.fixture(scope="session")
def session_keystore_execution():
    """Session-scoped keystore for the execution node."""
    with tempfile.TemporaryDirectory() as tempdir:
        keystore = Keystore.new("execution_node", "execution@test.local", path=tempdir, password="password")
        yield keystore


# ==============================================================================
# 2-Node Network Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def storage_node(session_keystore_storage):
    """Storage node with DOR enabled, RTI disabled.

    This node stores data objects but cannot execute jobs.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        local_ip = determine_local_ip()
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)

        _node = DefaultNode.create(
            keystore=session_keystore_storage,
            storage_path=tempdir,
            p2p_address=p2p_address,
            rest_address=rest_address,
            enable_db=True,
            dor_plugin_class=FilesystemDORService,
            rti_plugin_class=None,  # No RTI
            retain_job_history=False,
            strict_deployment=False
        )

        log.info(f"Storage node created: REST={rest_address}, P2P={p2p_address}")
        yield _node

        _node.shutdown()


@pytest.fixture(scope="session")
def execution_node(session_keystore_execution, storage_node):
    """Execution node with RTI enabled, DOR disabled.

    This node executes jobs but has no local storage.
    Joins the storage node's network to enable P2P.
    """
    with tempfile.TemporaryDirectory() as tempdir:
        local_ip = determine_local_ip()
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)

        _node = DefaultNode.create(
            keystore=session_keystore_execution,
            storage_path=tempdir,
            p2p_address=p2p_address,
            rest_address=rest_address,
            enable_db=True,
            dor_plugin_class=None,  # No DOR
            rti_plugin_class=DockerRTIService,
            retain_job_history=True,  # Keep job history for debugging
            strict_deployment=False
        )

        # Join the storage node's network
        run_coro_safely(_node.join_network(storage_node.rest.address()))

        # Verify network connectivity
        network = run_coro_safely(_node.db.get_network())
        assert len(network) == 2, f"Expected 2 nodes in network, got {len(network)}"

        log.info(f"Execution node created and joined network: REST={rest_address}, P2P={p2p_address}")
        yield _node

        _node.shutdown()


# ==============================================================================
# 2-Node Proxy Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def storage_dor_proxy(storage_node):
    """DOR proxy for the storage node."""
    return DORProxy(storage_node.rest.address())


@pytest.fixture(scope="session")
def storage_nodedb_proxy(storage_node):
    """NodeDB proxy for the storage node."""
    return NodeDBProxy(storage_node.rest.address())


@pytest.fixture(scope="session")
def execution_rti_proxy(execution_node):
    """RTI proxy for the execution node."""
    return RTIProxy(execution_node.rest.address())


@pytest.fixture(scope="session")
def execution_nodedb_proxy(execution_node):
    """NodeDB proxy for the execution node."""
    return NodeDBProxy(execution_node.rest.address())


# ==============================================================================
# 2-Node Data Directory Fixture
# ==============================================================================

@pytest.fixture(scope="session")
def session_data_dir_2node():
    """Session-scoped temporary directory for test data in 2-node setup."""
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


# ==============================================================================
# 2-Node Deployed Processor Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def deployed_abc_processor_2node(
    docker_available,
    storage_dor_proxy,
    execution_rti_proxy,
    storage_node,
    execution_node,
    session_data_dir_2node
) -> DataObject:
    """Session-scoped fixture that deploys the ABC processor in 2-node setup."""
    volumes = [ProcessorVolume(
        name='data_volume',
        mount_point='/data',
        read_only=False,
        reference={'path': session_data_dir_2node}
    )]

    with ProcessorDeployment2Node(
        proc_name='proc-abc',
        proc_path=PROC_ABC_PATH,
        storage_dor_proxy=storage_dor_proxy,
        execution_rti_proxy=execution_rti_proxy,
        storage_keystore=storage_node.keystore,
        execution_keystore=execution_node.keystore,
        docker_available=docker_available,
        volumes=volumes
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_room_processor_2node(
    docker_available,
    storage_dor_proxy,
    execution_rti_proxy,
    storage_node,
    execution_node
) -> DataObject:
    """Session-scoped fixture that deploys the Room processor in 2-node setup."""
    with ProcessorDeployment2Node(
        proc_name='proc-room',
        proc_path=PROC_ROOM_PATH,
        storage_dor_proxy=storage_dor_proxy,
        execution_rti_proxy=execution_rti_proxy,
        storage_keystore=storage_node.keystore,
        execution_keystore=execution_node.keystore,
        docker_available=docker_available
    ) as meta:
        yield meta


@pytest.fixture(scope="session")
def deployed_thermostat_processor_2node(
    docker_available,
    storage_dor_proxy,
    execution_rti_proxy,
    storage_node,
    execution_node
) -> DataObject:
    """Session-scoped fixture that deploys the Thermostat processor in 2-node setup."""
    with ProcessorDeployment2Node(
        proc_name='proc-thermostat',
        proc_path=PROC_THERMOSTAT_PATH,
        storage_dor_proxy=storage_dor_proxy,
        execution_rti_proxy=execution_rti_proxy,
        storage_keystore=storage_node.keystore,
        execution_keystore=execution_node.keystore,
        docker_available=docker_available
    ) as meta:
        yield meta


# ==============================================================================
# 2-Node Context Fixture
# ==============================================================================

@pytest.fixture(scope="session")
def rti_2node_context(
    storage_node,
    execution_node,
    storage_dor_proxy,
    execution_rti_proxy,
    storage_nodedb_proxy,
    execution_nodedb_proxy,
    deployed_abc_processor_2node,
    deployed_room_processor_2node,
    deployed_thermostat_processor_2node
) -> RTI2NodeContext:
    """Aggregated context for 2-node RTI tests.

    Provides all dependencies needed to run RTI tests against a 2-node setup
    where storage and execution are separated.
    """
    return RTI2NodeContext(
        storage_node=storage_node,
        execution_node=execution_node,
        storage_dor_proxy=storage_dor_proxy,
        execution_rti_proxy=execution_rti_proxy,
        storage_nodedb_proxy=storage_nodedb_proxy,
        execution_nodedb_proxy=execution_nodedb_proxy,
        deployed_abc_processor=deployed_abc_processor_2node,
        deployed_room_processor=deployed_room_processor_2node,
        deployed_thermostat_processor=deployed_thermostat_processor_2node,
        default_memory=1024
    )
