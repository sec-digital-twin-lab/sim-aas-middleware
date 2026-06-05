import time
import abc
import threading
from typing import Optional, Tuple

from simaas.rti.base import RTIServiceBase
from simaas.dor.schemas import DataObject
from simaas.core.errors import NotFoundError, AuthorisationError, OperationError, NetworkError
from simaas.dor.api import DORRESTService
from simaas.rti.schemas import Processor, BatchStatus
from simaas.core.helpers import get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.nodedb.api import NodeDBService, NodeDBProxy
from simaas.nodedb.protocol import P2PJoinNetwork, P2PLeaveNetwork, P2PUpdateIdentity
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.service import P2PService
from simaas.rest.service import RESTService
from simaas.meta import __version__

log = get_logger('simaas.node', 'node')


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

    def startup(self, p2p_address: str, rest_address: Tuple[str, int] = None,
                      bind_all_address: bool = False, wait_until_ready: bool = True) -> None:
        """
        Start the node's daemon services (P2P, REST).

        To join a network after startup, call `node.join_network(boot_node_address)` separately.
        """
        log.info('startup', f'Sim-aaS Middleware {__version__}')

        endpoints = []
        if self.db:
            log.info('startup', 'Enabling NodeDB service')
            endpoints += self.db.endpoints()

        if self.dor:
            log.info('startup', 'Enabling DOR service')
            endpoints += self.dor.endpoints()

        if self.rti:
            log.info('startup', 'Enabling RTI service')
            endpoints += self.rti.endpoints()

        log.info('startup', 'Starting P2P service')
        self.p2p = P2PService(self.keystore, p2p_address)
        self.p2p.set_node(self)
        for service in (self.db, self.dor, self.rti):
            if service is None:
                continue
            for protocol in service.get_p2p_protocols(self):
                self.p2p.add(protocol)
        self.p2p.start_service_background()

        if rest_address is not None:
            log.info('startup', 'Starting REST service')
            self.rest = RESTService(self, rest_address[0], rest_address[1], bind_all_address)
            self.rest.start_service()
            self.rest.add(endpoints)

        if wait_until_ready:
            log.info('startup', 'Waiting until node is ready')
            if not self.p2p.wait_until_ready(timeout=10.0):
                raise OperationError(
                    operation='node_startup',
                    stage='p2p_ready',
                    cause='timeout',
                    hint='P2P service failed to become ready'
                )
            if self.rest and not self.rest.wait_until_ready(timeout=10.0):
                raise OperationError(
                    operation='node_startup',
                    stage='rest_ready',
                    cause='timeout',
                    hint='REST service failed to become ready'
                )
            log.info('startup', 'Node is ready')

        # update our node db
        self.db.update_identity(self.identity)
        self.db.update_network(NodeInfo(
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

        Before calling shutdown, you should call:
        - `node.leave_network()` to inform peers
        - `node.shutdown_rti()` to undeploy processors
        """
        log.info('shutdown', 'Stopping all services')
        if self.p2p:
            self.p2p.stop_service()

        if self.rest:
            self.rest.stop_service()

    def shutdown_rti(self, timeout: int = 60) -> None:
        """
        Cleanup of RTI service: undeploy all processors and wait for workers.

        Call this before shutdown() to cleanly undeploy processors.
        """
        if self.rti is None:
            return

        # if we have any procs deployed, undeploy them
        for proc in self.rti.get_all_procs():
            proc_id: str = proc.id
            self.rti.undeploy(proc_id)

            # wait until proc is undeployed
            deadline = get_timestamp_now() + timeout * 1000
            check: Optional[Processor] = self.rti.get_proc(proc_id)
            while get_timestamp_now() < deadline and check is not None:
                time.sleep(1)
                check = self.rti.get_proc(proc_id)

            # successful?
            if check is not None:
                log.warning('shutdown', 'Undeploying processor failed', proc=proc_id, identity=self.identity.id)

        # wait for any active worker threads
        for _ in range(10):
            if not self.rti.has_active_workers():
                break
            log.info('shutdown', 'Waiting for active worker threads', identity=self.identity.id)
            time.sleep(1)
        else:
            log.warning('shutdown', 'Ignoring active worker threads still running', identity=self.identity.id)

    def join_network(self, boot_node_address: Tuple[str, int]) -> None:
        log.info('network', 'Joining network via boot node', boot_node=str(boot_node_address))

        try:
            # we only have an address, no node info. let's get info about the node first
            proxy = NodeDBProxy(boot_node_address)
            boot_node: NodeInfo = proxy.get_node()

        except NetworkError as e:
            log.error('network', 'Error connecting to boot node REST interface', reason=e.reason)
            return

        try:
            protocol = P2PJoinNetwork(self)
            protocol.perform(boot_node)
            network = self.db.get_network()
            log.info('network', 'Network joined', node_count=len(network))
        except NetworkError as e:
            log.error('network', 'Error during P2P network join', reason=e.reason)

    def leave_network(self, blocking: bool = False) -> None:
        try:
            protocol = P2PLeaveNetwork(self)
            protocol.perform(blocking=blocking)
        except NetworkError as e:
            log.error('network', 'Error during P2P network leave', reason=e.reason)

    def update_identity(self, name: str = None, email: str = None, propagate: bool = True) -> Identity:
        with self._mutex:
            # perform update on the keystore and update our own node db
            identity = self._keystore.update_profile(name=name, email=email)
            self.db.update_identity(identity)

            # propagate only if flag is set
            if propagate:
                try:
                    protocol = P2PUpdateIdentity(self)
                    network = self.db.get_network()
                    protocol.broadcast(network)
                except NetworkError as e:
                    log.error('identity', 'Error during P2P identity update', reason=e.reason)

            return identity

    def check_dor_ownership(self, obj_id: str, identity: Identity) -> None:
        # get the meta information of the object
        meta = self.dor.get_meta(obj_id)
        if meta is None:
            raise NotFoundError(resource_type='data_object', resource_id=obj_id)

        # check if the identity is the owner of that data object
        if meta.owner_iid != identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=obj_id,
                required_permission='owner'
            )

    def check_dor_has_access(self, obj_id: str, identity: Identity) -> None:
        # get the meta information of the object
        meta: DataObject = self.dor.get_meta(obj_id)
        if meta is None:
            raise NotFoundError(resource_type='data_object', resource_id=obj_id)

        # check if the identity has access to the data object content
        if meta.access_restricted and identity.id not in meta.access:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=obj_id,
                required_permission='access'
            )

    def check_rti_is_deployed(self, proc_id: str) -> None:
        if not self.rti.get_proc(proc_id):
            raise NotFoundError(resource_type='processor', resource_id=proc_id)

    def check_rti_not_busy(self, proc_id: str) -> None:
        proc: Processor = self.rti.get_proc(proc_id)
        if proc.state in [Processor.State.BUSY_DEPLOY, Processor.State.BUSY_UNDEPLOY]:
            raise OperationError(operation='deploy', stage='check', cause='processor busy')

    def check_rti_job_or_node_owner(self, job_id: str, identity: Identity) -> None:
        # get the job user (i.e., owner) and check if the caller user ids check out
        job_owner_iid = self.rti.get_job_owner_iid(job_id)
        if job_owner_iid != identity.id and identity.id != self.identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=job_id,
                required_permission='job_owner or node_owner'
            )

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
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=batch_id,
                required_permission='batch_owner, batch_member, or node_owner'
            )

    def check_rti_node_owner(self, identity: Identity) -> None:
        # check if the user is the owner of the node
        if self.identity.id != identity.id:
            raise AuthorisationError(
                identity_id=identity.id,
                resource_id=self.identity.id,
                required_permission='node_owner'
            )



