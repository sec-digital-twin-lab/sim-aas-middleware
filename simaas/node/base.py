import abc
import asyncio
import threading
import time
import traceback
from typing import Optional, Tuple

from simaas.dor.api import DORService
from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject, P2PPushDataObject
from simaas.nodedb.api import NodeDBService, NodeDBProxy
from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork, P2PUpdateIdentity
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.service import P2PService
from simaas.rest.service import RESTService
from simaas.rti.api import RTIService
from simaas.meta import __version__
from simaas.rti.protocol import P2PPushJobStatus

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
            strict_deployment=self.rti.strict_deployment if self.rti else None
        )

    def startup(self, p2p_address: str, rest_address: Tuple[str, int] = None,
                boot_node_address: (str, int) = None, bind_all_address: bool = False,
                wait_until_ready: bool = True) -> None:

        logger.info(f"Sim-aaS Middleware {__version__}")

        endpoints = []
        if self.db:
            logger.info("enabling NodeDB service.")
            endpoints += self.db.endpoints()

        if self.dor:
            logger.info("enabling DOR service.")
            endpoints += self.dor.endpoints()

        if self.rti:
            logger.info("enabling RTI service.")
            endpoints += self.rti.endpoints()

        logger.info("starting P2P service.")
        self.p2p = P2PService(self.keystore, p2p_address)
        self.p2p.add(P2PUpdateIdentity(self))
        self.p2p.add(P2PJoinNetwork(self))
        self.p2p.add(P2PLeaveNetwork(self))
        self.p2p.add(P2PLookupDataObject(self))
        self.p2p.add(P2PFetchDataObject(self))
        self.p2p.add(P2PPushDataObject(self))
        self.p2p.add(P2PPushJobStatus(self))
        self.p2p.start_service()

        if rest_address is not None:
            logger.info("starting REST service.")
            self.rest = RESTService(self, rest_address[0], rest_address[1], bind_all_address)
            self.rest.start_service()
            self.rest.add(endpoints)

        if wait_until_ready:
            logger.info("wait until node is ready...")
            while not self.p2p.is_ready() or (self.rest and not self.rest.is_ready()):
                time.sleep(0.5)
            logger.info("node is ready.")

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
            strict_deployment=self.rti.strict_deployment if self.rti else None
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

    def join_network(self, boot_node_address: Tuple[str, int]) -> None:
        logger.info(f"joining network via boot node: {boot_node_address}")

        try:
            # we only have an address, no node info. let's get info about the node first
            proxy = NodeDBProxy(boot_node_address)
            boot_node: NodeInfo = proxy.get_node()

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error("Error while connecting to boot node REST interface")
            logger.error(trace)
            return

        loop = asyncio.new_event_loop()
        try:
            protocol = P2PJoinNetwork(self)
            loop.run_until_complete(protocol.perform(boot_node))
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error("Error during P2P network join")
            logger.error(trace)
        finally:
            loop.close()

        found = '\n'.join([f"{n.identity.id}@{n.p2p_address}" for n in self.db.get_network()])
        logger.info(f"Nodes found:\n{found}")

    def leave_network(self, blocking: bool = False) -> None:
        loop = asyncio.new_event_loop()
        try:
            protocol = P2PLeaveNetwork(self)
            loop.run_until_complete(protocol.perform(blocking=blocking))
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error("Error during P2P network join")
            logger.error(trace)
        finally:
            loop.close()

    def update_identity(self, name: str = None, email: str = None, propagate: bool = True) -> Identity:
        with self._mutex:
            # perform update on the keystore and update our own node db
            identity = self._keystore.update_profile(name=name, email=email)
            self.db.update_identity(identity)

            # propagate only if flag is set
            if propagate:
                loop = asyncio.new_event_loop()
                try:
                    protocol = P2PUpdateIdentity(self)
                    loop.run_until_complete(protocol.broadcast(self.db.get_network()))
                except Exception as e:
                    trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                    logger.error("Error during P2P identity update")
                    logger.error(trace)
                finally:
                    loop.close()

            return identity
