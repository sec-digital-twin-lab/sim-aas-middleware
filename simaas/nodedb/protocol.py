import threading
from typing import Optional, List, Tuple, Dict

from pydantic import BaseModel

from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.decorators import p2p_public_access, p2p_requires_authentication
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress
from simaas.core.errors import NetworkError, AuthorisationError
from simaas.nodedb.schemas import NodeInfo, NamespaceInfo, ResourceDescriptor

log = get_logger('simaas.nodedb', 'nodedb')


class NodeDBSnapshot(BaseModel):
    update_identity: Optional[List[Identity]]
    update_network: Optional[List[NodeInfo]]
    update_namespace: Optional[List[NamespaceInfo]]


class UpdateIdentityMessage(BaseModel):
    identity: Identity


@p2p_public_access
# No header auth needed: the identity record is self-authenticating —
# Identity.verify_integrity() (called by node.db.update_identity) checks the
# embedded body signature against the record's own public key, and the
# monotonic nonce blocks rollback. Same model as the REST update_identity route.
class P2PUpdateIdentity(P2PProtocol):
    NAME = 'nodedb-update-id'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    def perform(self, peer: NodeInfo) -> Identity:
        peer_address = P2PAddress(
            address=peer.p2p_address,
            peer_tls_cert=peer.identity.tls_cert
        )

        message = UpdateIdentityMessage(identity=self._node.identity)

        reply, _ = p2p_request(
            peer_address, self.NAME, message, reply_type=UpdateIdentityMessage
        )
        reply: UpdateIdentityMessage = reply  # casting for PyCharm

        return reply.identity

    def broadcast(self, peers: List[NodeInfo]) -> List[Identity]:
        result: List[Identity] = []
        for peer in peers:
            try:
                peer_address = P2PAddress(
                    address=peer.p2p_address,
                    peer_tls_cert=peer.identity.tls_cert
                )

                message = UpdateIdentityMessage(identity=self._node.identity)

                reply, _ = p2p_request(
                    peer_address, self.NAME, message, reply_type=UpdateIdentityMessage
                )
                reply: UpdateIdentityMessage = reply  # casting for PyCharm

                result.append(reply.identity)
            except NetworkError as e:
                log.warning('broadcast', 'Peer unavailable for identity update', peer=peer.identity.id, reason=e.reason)

        return result

    def handle(
            self, request: UpdateIdentityMessage, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        log.info('identity', 'Received identity update from node', name=request.identity.name, id=request.identity.id)
        self._node.db.update_identity(request.identity)
        return UpdateIdentityMessage(identity=self._node.identity), None

    @staticmethod
    def request_type():
        return UpdateIdentityMessage

    @staticmethod
    def response_type():
        return UpdateIdentityMessage


class GetIdentityRequest(BaseModel):
    iid: str


class GetIdentityResponse(BaseModel):
    identity: Identity


@p2p_public_access
class P2PGetIdentity(P2PProtocol):
    NAME = 'nodedb-get-id'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(cls, peer_address: P2PAddress, iid: str) -> Optional[Identity]:
        reply, _ = p2p_request(
            peer_address, cls.NAME, GetIdentityRequest(iid=iid), reply_type=GetIdentityResponse
        )
        reply: GetIdentityResponse = reply  # casting for PyCharm

        return reply.identity

    def handle(
            self, request: GetIdentityRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        looked_up: Optional[Identity] = self._node.db.get_identity(request.iid)
        return GetIdentityResponse(identity=looked_up), None

    @staticmethod
    def request_type():
        return GetIdentityRequest

    @staticmethod
    def response_type():
        return GetIdentityResponse


class GetNetworkRequest(BaseModel):
    ...


class GetNetworkResponse(BaseModel):
    network: List[NodeInfo]


@p2p_public_access
class P2PGetNetwork(P2PProtocol):
    NAME = 'nodedb-get-network'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(cls, peer_address: P2PAddress) -> List[NodeInfo]:
        reply, _ = p2p_request(
            peer_address, cls.NAME, GetNetworkRequest(), reply_type=GetNetworkResponse
        )
        reply: GetNetworkResponse = reply  # casting for PyCharm

        return reply.network

    def handle(
            self, request: GetIdentityRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        network: List[NodeInfo] = self._node.db.get_network()
        return GetNetworkResponse(network=network), None

    @staticmethod
    def request_type():
        return GetNetworkRequest

    @staticmethod
    def response_type():
        return GetNetworkResponse


class PeerUpdateMessage(BaseModel):
    origin: NodeInfo
    snapshot: NodeDBSnapshot


@p2p_public_access
class P2PJoinNetwork(P2PProtocol):
    NAME = 'nodedb-join'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    def perform(self, boot_node: NodeInfo) -> NodeInfo:
        # send an update to the boot node, then proceed to send updates to all peers that discovered along the way
        remaining: List[NodeInfo] = [boot_node]
        processed: Dict[str, NodeInfo] = {self._node.identity.id: self._node.db.get_node()}
        while len(remaining) > 0:
            # have we already processed that peer?
            peer: NodeInfo = remaining.pop(0)
            if peer.identity.id in processed:
                continue
            else:
                processed[peer.identity.id] = peer

            # send the peer what we know about the network and the peer will reciprocate to update us on its
            # knowledge about the network.
            try:
                peer_address = P2PAddress(
                    address=peer.p2p_address,
                    peer_tls_cert=peer.identity.tls_cert
                )

                # create update message with a snapshot of the network, excluding nodes we already know about
                message = PeerUpdateMessage(
                    origin=self._node.db.get_node(),
                    snapshot=self._node.db.get_snapshot(exclude=list(processed.keys())),
                )

                # send update and wait for reply
                reply, _ = p2p_request(
                    peer_address, self.NAME, message, reply_type=PeerUpdateMessage
                )
                reply: PeerUpdateMessage = reply  # casing for PyCharm

                # update the db information about the originator
                self._node.db.update_identity(reply.origin.identity)
                self._node.db.update_network(reply.origin)

                # process the snapshot identities (if any)
                if reply.snapshot.update_identity:
                    for identity in reply.snapshot.update_identity:
                        self._node.db.update_identity(identity)

                # process the snapshot nodes (if any)
                if reply.snapshot.update_network:
                    for node in reply.snapshot.update_network:
                        remaining.append(node)

                # process the namespaces (if any)
                if reply.snapshot.update_namespace:
                    for ns_info in reply.snapshot.update_namespace:
                        self._node.db.handle_namespace_snapshot(ns_info)

                log.debug(f"Adding peer at {peer.p2p_address} to db", name=peer.identity.name, id=peer.identity.id)

            except NetworkError:
                log.debug(f"Peer at {peer.p2p_address} unavailable, removing from NodeDB")
                self._node.db.remove_node_by_address(peer.p2p_address)

            # get all nodes in the network and add any nodes that we may not have been aware of
            for node in self._node.db.get_network():
                if node.identity.id not in processed:
                    remaining.append(node)

        return boot_node

    def handle(
            self, request: PeerUpdateMessage, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # update the db information about the originator
        self._node.db.update_identity(request.origin.identity)
        self._node.db.update_network(request.origin)

        # process the snapshot identities (if any)
        if request.snapshot.update_identity:
            for snap_identity in request.snapshot.update_identity:
                self._node.db.update_identity(snap_identity)

        # process the snapshot nodes (if any)
        if request.snapshot.update_network:
            for node in request.snapshot.update_network:
                self._node.db.update_network(node)

        # process the namespaces (if any)
        if request.snapshot.update_namespace:
            for ns_info in request.snapshot.update_namespace:
                self._node.db.handle_namespace_snapshot(ns_info)

        return PeerUpdateMessage(
            origin=self._node.db.get_node(),
            snapshot=self._node.db.get_snapshot(exclude=[self._node.identity.id, request.origin.identity.id])
        ), None

    @staticmethod
    def request_type():
        return PeerUpdateMessage

    @staticmethod
    def response_type():
        return PeerUpdateMessage


class PeerLeaveMessage(BaseModel):
    origin: NodeInfo


@p2p_public_access
# TODO(security): require auth + check request.origin.identity.id == signer.id.
class P2PLeaveNetwork(P2PProtocol):
    NAME = 'nodedb-leave'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    def perform(self, blocking: bool = False) -> None:
        message = PeerLeaveMessage(origin=self._node.db.get_node())
        for peer in self._node.db.get_network():
            if peer.identity.id != message.origin.identity.id:
                peer_address = P2PAddress(
                    address=peer.p2p_address,
                    peer_tls_cert=peer.identity.tls_cert
                )

                if blocking:
                    p2p_request(peer_address, self.NAME, message)
                else:
                    def _fire_and_forget(addr=peer_address, msg=message):
                        try:
                            p2p_request(addr, self.NAME, msg)
                        except Exception as e:
                            log.warning('leave', 'Failed to notify peer of leave', exc=e)

                    threading.Thread(target=_fire_and_forget, daemon=True).start()

    def handle(
            self, request: PeerLeaveMessage, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self._node.db.update_identity(request.origin.identity)
        self._node.db.remove_node_by_id(request.origin.identity)
        return None, None

    @staticmethod
    def request_type():
        return PeerLeaveMessage

    @staticmethod
    def response_type():
        return None


class UpdateNamespaceBudgetRequest(BaseModel):
    namespace: str
    budget: ResourceDescriptor


@p2p_requires_authentication
# TODO(security): tighten once namespaces have an ownership model;
# P2PReserveNamespaceResources / Cancel inherit the same TODO.
class P2PUpdateNamespaceBudget(P2PProtocol):
    NAME = 'nodedb-namespace-update'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(
            cls, node, peer: NodeInfo, namespace: str, budget: ResourceDescriptor,
    ) -> None:
        # get the fully qualified P2P address for the peer
        peer_address = P2PAddress(
            address=peer.p2p_address,
            peer_tls_cert=peer.identity.tls_cert
        )

        try:
            # send the request, signed by the local node's keystore
            reply, _ = p2p_request(
                peer_address, cls.NAME, UpdateNamespaceBudgetRequest(namespace=namespace, budget=budget),
                with_authorisation_by=node.keystore,
            )

        except NetworkError as e:
            log.warning('namespace', 'Peer unavailable for namespace budget update', peer=peer.identity.id, namespace=namespace, reason=e.reason)

    def handle(
            self, request: UpdateNamespaceBudgetRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self._node.db.handle_namespace_update(request.namespace, request.budget)
        return None, None

    @staticmethod
    def request_type():
        return UpdateNamespaceBudgetRequest

    @staticmethod
    def response_type():
        return None


class ResourceReservationRequest(BaseModel):
    namespace: str
    job_id: str
    resources: ResourceDescriptor


class ResourceReservationReply(BaseModel):
    accepted: bool


@p2p_requires_authentication
class P2PReserveNamespaceResources(P2PProtocol):
    NAME = 'nodedb-namespace-reserve'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(
            cls, node, peer: NodeInfo, namespace: str, job_id: str, resources: ResourceDescriptor,
    ) -> bool:
        # get the fully qualified P2P address for the peer
        peer_address = P2PAddress(
            address=peer.p2p_address,
            peer_tls_cert=peer.identity.tls_cert
        )

        try:
            # send the request signed by the local node's keystore
            reply, _ = p2p_request(
                peer_address, cls.NAME, ResourceReservationRequest(
                    namespace=namespace, job_id=job_id, resources=resources
                ), reply_type=ResourceReservationReply,
                with_authorisation_by=node.keystore,
            )
            reply: ResourceReservationReply = reply  # casting for PyCharm
            return reply.accepted

        except NetworkError as e:
            log.warning('namespace', 'Peer unavailable for resource reservation', peer=peer.identity.id, namespace=namespace, job=job_id, reason=e.reason)
            return False

    def handle(
            self, request: ResourceReservationRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        accepted: bool = self._node.db.handle_namespace_reservation(
            request.namespace, request.job_id, request.resources
        )
        return ResourceReservationReply(accepted=accepted), None

    @staticmethod
    def request_type():
        return ResourceReservationRequest

    @staticmethod
    def response_type():
        return ResourceReservationReply


class ResourceReservationCancellation(BaseModel):
    namespace: str
    job_id: str


@p2p_requires_authentication
class P2PCancelNamespaceReservation(P2PProtocol):
    NAME = 'nodedb-namespace-cancel'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(cls, node, peer: NodeInfo, namespace: str, job_id: str) -> None:
        # get the fully qualified P2P address for the peer
        peer_address = P2PAddress(
            address=peer.p2p_address,
            peer_tls_cert=peer.identity.tls_cert
        )

        try:
            # send the request signed by the local node's keystore
            reply, _ = p2p_request(
                peer_address, cls.NAME, ResourceReservationCancellation(namespace=namespace, job_id=job_id),
                with_authorisation_by=node.keystore,
            )

        except NetworkError as e:
            log.warning('namespace', 'Peer unavailable for reservation cancellation', peer=peer.identity.id, namespace=namespace, job=job_id, reason=e.reason)

    def handle(
            self, request: ResourceReservationCancellation, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self._node.db.handle_namespace_cancellation(request.namespace, request.job_id)
        return None, None

    @staticmethod
    def request_type():
        return ResourceReservationCancellation

    @staticmethod
    def response_type():
        return None
