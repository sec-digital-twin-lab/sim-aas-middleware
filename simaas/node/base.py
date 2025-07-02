import abc
import asyncio
import threading
import time
import traceback
from typing import Optional, Tuple

from simaas.rti.exceptions import ProcessorNotDeployedError, ProcessorBusyError
from simaas.dor.schemas import DataObject
from simaas.rest.exceptions import AuthorisationFailedError
from simaas.dor.exceptions import DataObjectNotFoundError
from simaas.dor.api import DORRESTService
from simaas.namespace.protocol import P2PNamespaceServiceCall
from simaas.rti.schemas import Processor, BatchStatus
from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject, P2PPushDataObject
from simaas.nodedb.api import NodeDBService, NodeDBProxy
from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork, P2PUpdateIdentity, P2PGetIdentity, P2PGetNetwork, \
    P2PReserveNamespaceResources, P2PCancelNamespaceReservation, P2PUpdateNamespaceBudget
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.service import P2PService
from simaas.rest.service import RESTService
from simaas.rti.api import RTIRESTService
from simaas.meta import __version__
from simaas.rti.protocol import P2PPushJobStatus, P2PRunnerPerformHandshake

logger = Logging.get('node.base')


class Node(abc.ABC):
    def __init__(self, keystore: Keystore) -> None:
        self._mutex = threading.Lock()
        self._keystore = keystore
        self.p2p: Optional[P2PService] = None
        self.rest: Optional[RESTService] = None
        self.db: Optional[NodeDBService] = None
        self.dor: Optional[DORRESTService] = None
        self.rti: Optional[RTIRESTService] = None

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
        self.p2p.add(P2PGetIdentity(self))
        self.p2p.add(P2PGetNetwork(self))
        self.p2p.add(P2PRunnerPerformHandshake(self))
        self.p2p.add(P2PNamespaceServiceCall(self))
        self.p2p.add(P2PUpdateNamespaceBudget(self))
        self.p2p.add(P2PReserveNamespaceResources(self))
        self.p2p.add(P2PCancelNamespaceReservation(self))
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

    def shutdown(self, leave_network: bool = True, timeout: int = 60) -> None:
        # leave the network
        if leave_network:
            self.leave_network()
        else:
            logger.warning("node shutting down silently (not leaving the network)")

        # if we have any procs deployed, undeploy them
        if self.rti is not None:
            for proc in self.rti.get_all_procs():
                proc_id: str = proc.id
                self.rti.undeploy(proc_id)

                # wait until proc is undeployed
                deadline = get_timestamp_now() + timeout * 1000
                check: Optional[Processor] = self.rti.get_proc(proc_id)
                while get_timestamp_now() < deadline and check is not None:
                    time.sleep(1)
                    check: Optional[Processor] = self.rti.get_proc(proc_id)

                # successful?
                if check is not None:
                    logger.warning(f"undeploying processor {proc_id} failed.")

        # stop the services
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

    def check_dor_ownership(self, obj_id: str, identity: Identity) -> None:
        # get the meta information of the object
        meta = self.dor.get_meta(obj_id)
        if meta is None:
            raise DataObjectNotFoundError(obj_id)

        # check if the identity is the owner of that data object
        if meta.owner_iid != identity.id:
            raise AuthorisationFailedError({
                'reason': 'user is not the data object owner',
                'obj_id': obj_id,
                'user_iid': identity.id
            })

    def check_dor_has_access(self, obj_id: str, identity: Identity) -> None:
        # get the meta information of the object
        meta: DataObject = self.dor.get_meta(obj_id)
        if meta is None:
            raise AuthorisationFailedError({
                'reason': 'data object does not exist',
                'obj_id': obj_id
            })

        # check if the identity has access to the data object content
        if meta.access_restricted and identity.id not in meta.access:
            raise AuthorisationFailedError({
                'reason': 'user has no access to the data object content',
                'obj_id': obj_id,
                'user_iid': identity.id
            })

    def check_rti_is_deployed(self, proc_id: str) -> None:
        if not self.rti.get_proc(proc_id):
            raise ProcessorNotDeployedError({
                'proc_id': proc_id
            })

    def check_rti_not_busy(self, proc_id: str) -> None:
        proc: Processor = self.rti.get_proc(proc_id)
        if proc.state in [Processor.State.BUSY_DEPLOY, Processor.State.BUSY_UNDEPLOY]:
            raise ProcessorBusyError({
                'proc_id': proc_id
            })

    def check_rti_job_or_node_owner(self, job_id: str, identity: Identity) -> None:
        # get the job user (i.e., owner) and check if the caller user ids check out
        job_owner_iid = self.rti.get_job_owner_iid(job_id)
        if job_owner_iid != identity.id and identity.id != self.identity.id:
            raise AuthorisationFailedError({
                'reason': 'user is not the job owner or the node owner',
                'job_id': job_id,
                'job_owner_iid': job_owner_iid,
                'request_user_iid': identity.id,
                'node_iid': self.identity.id
            })

    def check_rti_batch_or_node_owner(self, batch_id: str, identity: Identity) -> None:
        # get the batch status to determine the owner iid
        batch_status: BatchStatus = self.rti.get_batch_status(batch_id)
        batch_owner_iid = batch_status.user_iid

        # check if the identity is part of the batch
        for member in batch_status.members:
            if member.identity and member.identity.id == identity.id:
                return

        # get the job user (i.e., owner) and check if the caller user ids check out
        if batch_owner_iid != identity.id and identity.id != self.identity.id:
            raise AuthorisationFailedError({
                'reason': 'user is not the batch owner, batch member or the node owner',
                'batch_id': batch_id,
                'batch_owner_iid': batch_owner_iid,
                'request_user_iid': identity.id,
                'node_iid': self.identity.id
            })

    def check_rti_node_owner(self, identity: Identity) -> None:
        # check if the user is the owner of the node
        if self.identity.id != identity.id:
            raise AuthorisationFailedError({
                'reason': 'User is not the node owner',
                'user_iid': identity.id,
                'node_iid': self.identity.id
            })



