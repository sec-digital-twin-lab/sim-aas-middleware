import abc
import threading
import time
from typing import Optional

from saas.dor.api import DORService
from saas.core.helpers import get_timestamp_now
from saas.core.identity import Identity
from saas.core.keystore import Keystore
from saas.core.logging import Logging
from saas.nodedb.api import NodeDBService
from saas.nodedb.schemas import NodeInfo
from saas.p2p.exceptions import P2PException, BootNodeUnavailableError
from saas.p2p.service import P2PService
from saas.rest.service import RESTService
from saas.rti.api import RTIService
from saas.meta import __version__

logger = Logging.get('node.base')


class Node(abc.ABC):
    def __init__(self, keystore: Keystore) -> None:
        self._mutex = threading.Lock()
        self._keystore = keystore
        self.p2p: Optional[P2PService] = None
        self.rest: Optional[RESTService] = None
        self.db: Optional[NodeDBService] = None
        self.dor: Optional[DORService] = None
        self.rti: Optional[RTIService] = None

    @property
    def keystore(self) -> Keystore:
        return self._keystore

    @property
    def identity(self) -> Identity:
        return self._keystore.identity

    @property
    def info(self) -> NodeInfo:
        return NodeInfo(
            identity=self.identity,
            last_seen=get_timestamp_now(),
            dor_service=self.dor is not None,
            rti_service=self.rti is not None,
            p2p_address=self.p2p.address(),
            rest_address=self.rest.address() if self.rest else None,
            retain_job_history=self.rti.retain_job_history if self.rti else None,
            strict_deployment=self.rti.strict_deployment if self.rti else None,
            job_concurrency=self.rti.job_concurrency if self.rti else None
        )

    def startup(self, p2p_address: (str, int), rest_address: (str, int) = None,
                boot_node_address: (str, int) = None, bind_all_address: bool = False) -> None:

        logger.info(f"sim-aas-middleware {__version__}")

        logger.info("starting P2P service.")
        self.p2p = P2PService(self, p2p_address, bind_all_address)
        self.p2p.start_service()

        endpoints = []
        if self.db:
            logger.info(f"enabling NodeDB service.")
            self.p2p.add(self.db.protocol)
            endpoints += self.db.endpoints()

        if self.dor:
            logger.info(f"enabling DOR service.")
            self.p2p.add(self.dor.protocol)
            endpoints += self.dor.endpoints()

        if self.rti:
            logger.info(f"enabling RTI service.")
            endpoints += self.rti.endpoints()

        if rest_address is not None:
            logger.info("starting REST service.")
            self.rest = RESTService(self, rest_address[0], rest_address[1], bind_all_address)
            self.rest.start_service()
            self.rest.add(endpoints)

        # update our node db
        self.db.update_identity(self.identity)
        self.db.update_network(NodeInfo(
            identity=self.identity,
            last_seen=get_timestamp_now(),
            dor_service=self.dor is not None,
            rti_service=self.rti is not None,
            p2p_address=self.p2p.address(),
            rest_address=self.rest.address() if self.rest else None,
            retain_job_history=self.rti.retain_job_history if self.rti else None,
            strict_deployment=self.rti.strict_deployment if self.rti else None,
            job_concurrency=self.rti.job_concurrency if self.rti else None
        ))

        # join an existing network of nodes?
        if boot_node_address:
            self.join_network(boot_node_address)

    def shutdown(self, leave_network: bool = True) -> None:
        if leave_network:
            self.leave_network()
        else:
            logger.warning("node shutting down silently (not leaving the network)")

        logger.info("stopping all services.")
        if self.p2p:
            self.p2p.stop_service()

        if self.rest:
            self.rest.stop_service()

    def join_network(self, boot_node_address: (str, int)) -> None:
        logger.info(f"joining network via boot node: {boot_node_address}")
        connected = False
        retries = 0
        while not connected:
            try:
                boot_identity = self.db.protocol.ping_node(boot_node_address)
                logger.info(f"boot node found: {boot_identity.name} | {boot_identity.id}")
                connected = True
            except P2PException as e:
                logger.info(f"Unable to connect to boot node: {boot_node_address}")
                retries += 1
                if retries == 5:
                    logger.info("Retry stopped")
                    raise BootNodeUnavailableError({
                        "boot_node_address": boot_node_address
                    }) from e
                logger.info("Retry joining network")
                time.sleep(2)

        self.db.protocol.perform_join(boot_node_address)
        logger.info(f"Nodes found in network: {[n.p2p_address for n in self.db.get_network()]}")

    def leave_network(self) -> None:
        self.db.protocol.perform_leave()

    def update_identity(self, name: str = None, email: str = None, propagate: bool = True) -> Identity:
        with self._mutex:
            # perform update on the keystore
            identity = self._keystore.update_profile(name=name, email=email)

            # user the identity and update the node db
            self.db.update_identity(identity)

            # propagate only if flag is set
            if propagate:
                self.db.protocol.broadcast_identity_update(identity)

            return identity
