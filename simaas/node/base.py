import abc
import asyncio
import threading
from typing import Optional, Tuple

from simaas.rti.base import RTIServiceBase
from simaas.dor.schemas import DataObject
from simaas.core.errors import NotFoundError, AuthorisationError, OperationError, NetworkError
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
        self.rti: Optional[RTIServiceBase] = None

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
            dor_service=self.dor.type() if self.dor else 'none',
            rti_service=self.rti.type() if self.rti else 'none',
            p2p_address=self.p2p.address(),
            rest_address=self.rest.address() if self.rest else None,
            retain_job_history=self.rti.retain_job_history if self.rti else None,
            strict_deployment=self.rti.strict_deployment if self.rti else None
        )

    async def startup(self, p2p_address: str, rest_address: Tuple[str, int] = None,
                      bind_all_address: bool = False, wait_until_ready: bool = True) -> None:
        """
        Start the node's daemon services (P2P, REST).

        This is an async method that manages thread lifecycle. To join a network after startup,
        call `await node.join_network(boot_node_address)` separately.
        """
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
        self.p2p.start_service_background()

        if rest_address is not None:
            logger.info("starting REST service.")
            self.rest = RESTService(self, rest_address[0], rest_address[1], bind_all_address)
            self.rest.start_service()
            self.rest.add(endpoints)

        if wait_until_ready:
            logger.info("wait until node is ready...")
            if not await self.p2p.wait_until_ready(timeout=10.0):
                raise OperationError(
                    operation='node_startup',
                    stage='p2p_ready',
                    cause='timeout',
                    hint='P2P service failed to become ready'
                )
            if self.rest and not await self.rest.wait_until_ready(timeout=10.0):
                raise OperationError(
                    operation='node_startup',
                    stage='rest_ready',
                    cause='timeout',
                    hint='REST service failed to become ready'
                )
            logger.info("node is ready.")

        # update our node db
        await self.db.update_identity(self.identity)
        await self.db.update_network(NodeInfo(
            identity=self.identity,
            last_seen=get_timestamp_now(),
            dor_service=self.dor.type() if self.dor else 'none',
            rti_service=self.rti.type() if self.rti else 'none',
            p2p_address=self.p2p.address(),
            rest_address=self.rest.address() if self.rest else None,
            retain_job_history=self.rti.retain_job_history if self.rti else None,
            strict_deployment=self.rti.strict_deployment if self.rti else None
        ))

    def shutdown(self) -> None:
        """
        Stop the node's daemon services (P2P, REST).

        This is a sync method that manages thread lifecycle. Before calling shutdown,
        you should call async cleanup methods:
        - `await node.leave_network()` to inform peers
        - `await node.shutdown_rti()` to undeploy processors
        """
        logger.info("stopping all services.")
        if self.p2p:
            self.p2p.stop_service()

        if self.rest:
            self.rest.stop_service()

    async def shutdown_rti(self, timeout: int = 60) -> None:
        """
        Async cleanup of RTI service: undeploy all processors and wait for workers.

        Call this before shutdown() to cleanly undeploy processors.
        """
        if self.rti is None:
            return

        # if we have any procs deployed, undeploy them
        for proc in await self.rti.get_all_procs():
            proc_id: str = proc.id
            await self.rti.undeploy(proc_id)

            # wait until proc is undeployed
            deadline = get_timestamp_now() + timeout * 1000
            check: Optional[Processor] = await self.rti.get_proc(proc_id)
            while get_timestamp_now() < deadline and check is not None:
                await asyncio.sleep(1)
                check = await self.rti.get_proc(proc_id)

            # successful?
            if check is not None:
                logger.warning(
                    f"[{self.identity.id}/{self.identity.name}] shutdown_rti: undeploying processor {proc_id} failed."
                )

        # wait for any active worker threads
        for _ in range(10):
            if not self.rti.has_active_workers():
                break
            logger.info(
                f"[{self.identity.id}/{self.identity.name}] shutdown_rti: waiting for active worker threads to be done..."
            )
            await asyncio.sleep(1)
        else:
            logger.warning(
                f"[{self.identity.id}/{self.identity.name}] shutdown_rti: ignoring active worker threads that are still active."
            )

    async def join_network(self, boot_node_address: Tuple[str, int]) -> None:
        logger.info(f"joining network via boot node: {boot_node_address}")

        try:
            # we only have an address, no node info. let's get info about the node first
            proxy = NodeDBProxy(boot_node_address)
            boot_node: NodeInfo = proxy.get_node()

        except NetworkError as e:
            logger.error(f"Error while connecting to boot node REST interface: {e.reason}")
            return

        try:
            protocol = P2PJoinNetwork(self)
            await protocol.perform(boot_node)
            network = await self.db.get_network()
            found = '\n'.join([f"{n.identity.id}@{n.p2p_address}" for n in network])
            logger.info(f"Nodes found:\n{found}")
        except NetworkError as e:
            logger.error(f"Error during P2P network join: {e.reason}")

    async def leave_network(self, blocking: bool = False) -> None:
        try:
            protocol = P2PLeaveNetwork(self)
            await protocol.perform(blocking=blocking)
        except NetworkError as e:
            logger.error(f"Error during P2P network leave: {e.reason}")

    async def update_identity(self, name: str = None, email: str = None, propagate: bool = True) -> Identity:
        with self._mutex:
            # perform update on the keystore and update our own node db
            identity = self._keystore.update_profile(name=name, email=email)
            await self.db.update_identity(identity)

            # propagate only if flag is set
            if propagate:
                try:
                    protocol = P2PUpdateIdentity(self)
                    network = await self.db.get_network()
                    await protocol.broadcast(network)
                except NetworkError as e:
                    logger.error(f"Error during P2P identity update: {e.reason}")

            return identity

    async def check_dor_ownership(self, obj_id: str, identity: Identity) -> None:
        # get the meta information of the object
        meta = await self.dor.get_meta(obj_id)
        if meta is None:
            raise NotFoundError(resource_type='data_object', resource_id=obj_id)

        # check if the identity is the owner of that data object
        if meta.owner_iid != identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=obj_id,
                required_permission='owner'
            )

    async def check_dor_has_access(self, obj_id: str, identity: Identity) -> None:
        # get the meta information of the object
        meta: DataObject = await self.dor.get_meta(obj_id)
        if meta is None:
            raise NotFoundError(resource_type='data_object', resource_id=obj_id)

        # check if the identity has access to the data object content
        if meta.access_restricted and identity.id not in meta.access:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=obj_id,
                required_permission='access'
            )

    async def check_rti_is_deployed(self, proc_id: str) -> None:
        if not await self.rti.get_proc(proc_id):
            raise NotFoundError(resource_type='processor', resource_id=proc_id)

    async def check_rti_not_busy(self, proc_id: str) -> None:
        proc: Processor = await self.rti.get_proc(proc_id)
        if proc.state in [Processor.State.BUSY_DEPLOY, Processor.State.BUSY_UNDEPLOY]:
            raise OperationError(operation='deploy', stage='check', cause='processor busy')

    async def check_rti_job_or_node_owner(self, job_id: str, identity: Identity) -> None:
        # get the job user (i.e., owner) and check if the caller user ids check out
        job_owner_iid = await self.rti.get_job_owner_iid(job_id)
        if job_owner_iid != identity.id and identity.id != self.identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=job_id,
                required_permission='job_owner or node_owner'
            )

    async def check_rti_batch_or_node_owner(self, batch_id: str, identity: Identity) -> None:
        # get the batch status to determine the owner iid
        batch_status: BatchStatus = await self.rti.get_batch_status(batch_id)
        batch_owner_iid = batch_status.user_iid

        # check if the identity is part of the batch
        for member in batch_status.members:
            if member.identity and member.identity.id == identity.id:
                return

        # get the job user (i.e., owner) and check if the caller user ids check out
        if batch_owner_iid != identity.id and identity.id != self.identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=batch_id,
                required_permission='batch_owner, batch_member, or node_owner'
            )

    async def check_rti_node_owner(self, identity: Identity) -> None:
        # check if the user is the owner of the node
        if self.identity.id != identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=self.identity.id,
                required_permission='node_owner'
            )



