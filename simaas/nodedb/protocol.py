import asyncio
import traceback
from typing import Optional, List, Tuple, Dict

from pydantic import BaseModel

from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress
from simaas.p2p.exceptions import PeerUnavailableError
from simaas.nodedb.schemas import NodeInfo, NamespaceInfo, ResourceDescriptor

logger = Logging.get('nodedb.protocol')


class NodeDBSnapshot(BaseModel):
    update_identity: Optional[List[Identity]]
    update_network: Optional[List[NodeInfo]]
    update_namespace: Optional[List[NamespaceInfo]]


class UpdateIdentityMessage(BaseModel):
    identity: Identity


class P2PUpdateIdentity(P2PProtocol):
    NAME = 'nodedb-update-id'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    async def perform(self, peer: NodeInfo) -> Identity:
        peer_address = P2PAddress(
            address=peer.p2p_address,
            curve_secret_key=self._node.keystore.curve_secret_key(),
            curve_public_key=self._node.keystore.curve_public_key(),
            curve_server_key=peer.identity.c_public_key
        )

        message = UpdateIdentityMessage(identity=self._node.identity)

        reply, _ = await p2p_request(
            peer_address, self.NAME, message, reply_type=UpdateIdentityMessage
        )
        reply: UpdateIdentityMessage = reply  # casting for PyCharm

        return reply.identity

    async def broadcast(self, peers: List[NodeInfo]) -> List[Identity]:
        result: List[Identity] = []
        for peer in peers:
            try:
                peer_address = P2PAddress(
                    address=peer.p2p_address,
                    curve_secret_key=self._node.keystore.curve_secret_key(),
                    curve_public_key=self._node.keystore.curve_public_key(),
                    curve_server_key=peer.identity.c_public_key
                )

                message = UpdateIdentityMessage(identity=self._node.identity)

                reply, _ = await p2p_request(
                    peer_address, self.NAME, message, reply_type=UpdateIdentityMessage
                )
                reply: UpdateIdentityMessage = reply  # casting for PyCharm

                result.append(reply.identity)
            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
                logger.error(f"Failed to send identity update to: {peer.identity.name} | {peer.identity.id}")
                logger.error(trace)

        return result

    async def handle(
            self, request: UpdateIdentityMessage, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        logger.info(f"Received identity update from node: {request.identity.name} | {request.identity.id}")
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


class P2PGetIdentity(P2PProtocol):
    NAME = 'nodedb-get-id'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    async def perform(cls, peer_address: P2PAddress, iid: str) -> Optional[Identity]:
        reply, _ = await p2p_request(
            peer_address, cls.NAME, GetIdentityRequest(iid=iid), reply_type=GetIdentityResponse
        )
        reply: GetIdentityResponse = reply  # casting for PyCharm

        return reply.identity

    async def handle(
            self, request: GetIdentityRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        identity: Optional[Identity] = self._node.db.get_identity(request.iid)
        return GetIdentityResponse(identity=identity), None

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


class P2PGetNetwork(P2PProtocol):
    NAME = 'nodedb-get-network'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    async def perform(cls, peer_address: P2PAddress) -> List[NodeInfo]:
        reply, _ = await p2p_request(
            peer_address, cls.NAME, GetNetworkRequest(), reply_type=GetNetworkResponse
        )
        reply: GetNetworkResponse = reply  # casting for PyCharm

        return reply.network

    async def handle(
            self, request: GetIdentityRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
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


class P2PJoinNetwork(P2PProtocol):
    NAME = 'nodedb-join'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    async def perform(self, boot_node: NodeInfo) -> NodeInfo:
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
                    curve_secret_key=self._node.keystore.curve_secret_key(),
                    curve_public_key=self._node.keystore.curve_public_key(),
                    curve_server_key=peer.identity.c_public_key
                )

                # create update message with a snapshot of the network, excluding nodes we already know about
                message = PeerUpdateMessage(
                    origin=self._node.db.get_node(),
                    snapshot=self._node.db.get_snapshot(exclude=list(processed.keys())),
                )

                # send update and wait for reply
                reply, _ = await p2p_request(
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

                logger.debug(f"Adding peer at {peer.p2p_address} to db: {peer.identity.name} | {peer.identity.id}")

            except PeerUnavailableError:
                logger.debug(f"Peer at {peer.p2p_address} unavailable -> Removing from NodeDB.")
                self._node.db.remove_node_by_address(peer.p2p_address)

            # get all nodes in the network and add any nodes that we may not have been aware of
            for node in self._node.db.get_network():
                if node.identity.id not in processed:
                    remaining.append(node)

        return boot_node

    async def handle(
            self, request: PeerUpdateMessage, attachment_path: Optional[str] = None, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # update the db information about the originator
        self._node.db.update_identity(request.origin.identity)
        self._node.db.update_network(request.origin)

        # process the snapshot identities (if any)
        if request.snapshot.update_identity:
            for identity in request.snapshot.update_identity:
                self._node.db.update_identity(identity)

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


class P2PLeaveNetwork(P2PProtocol):
    NAME = 'nodedb-leave'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    async def perform(self, blocking: bool = False) -> None:
        message = PeerLeaveMessage(origin=self._node.db.get_node())
        for peer in self._node.db.get_network():
            if peer.identity.id != message.origin.identity.id:
                peer_address = P2PAddress(
                    address=peer.p2p_address,
                    curve_secret_key=self._node.keystore.curve_secret_key(),
                    curve_public_key=self._node.keystore.curve_public_key(),
                    curve_server_key=peer.identity.c_public_key
                )

                if blocking:
                    await p2p_request(peer_address, self.NAME, message)
                else:
                    asyncio.create_task(p2p_request(peer_address, self.NAME, message))

    async def handle(
            self, request: PeerLeaveMessage, attachment_path: Optional[str] = None, download_path: Optional[str] = None
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


class P2PUpdateNamespaceBudget(P2PProtocol):
    NAME = 'nodedb-namespace-update'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    async def perform(
            cls, node, peer: NodeInfo, namespace: str, budget: ResourceDescriptor
    ) -> None:
        # get the fully qualified P2P address for the peer
        peer_address = P2PAddress(
            address=peer.p2p_address,
            curve_secret_key=node.keystore.curve_secret_key(),
            curve_public_key=node.keystore.curve_public_key(),
            curve_server_key=peer.identity.c_public_key
        )

        try:
            # send the request
            reply, _ = await p2p_request(
                peer_address, cls.NAME, UpdateNamespaceBudgetRequest(namespace=namespace, budget=budget)
            )

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            logger.error(f"Failed to send update namespace budget request for {namespace} "
                         f"to: {peer.identity.name} | {peer.identity.id}")
            logger.error(trace)

    async def handle(
            self, request: UpdateNamespaceBudgetRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        try:
            self._node.db.handle_namespace_update(request.namespace, request.budget)
            return None, None

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            logger.error(trace)
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


class P2PReserveNamespaceResources(P2PProtocol):
    NAME = 'nodedb-namespace-reserve'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    async def perform(
            cls, node, peer: NodeInfo, namespace: str, job_id: str, resources: ResourceDescriptor
    ) -> bool:
        # get the fully qualified P2P address for the peer
        peer_address = P2PAddress(
            address=peer.p2p_address,
            curve_secret_key=node.keystore.curve_secret_key(),
            curve_public_key=node.keystore.curve_public_key(),
            curve_server_key=peer.identity.c_public_key
        )

        try:
            # send the request
            reply, _ = await p2p_request(
                peer_address, cls.NAME, ResourceReservationRequest(
                    namespace=namespace, job_id=job_id, resources=resources
                ), reply_type=ResourceReservationReply
            )
            reply: ResourceReservationReply = reply  # casting for PyCharm
            return reply.accepted

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            logger.error(f"Failed to send resource reservation request for {namespace}:{job_id}' "
                         f"to: {peer.identity.name} | {peer.identity.id}")
            logger.error(trace)
            return False

    async def handle(
            self, request: ResourceReservationRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        try:
            accepted: bool = self._node.db.handle_namespace_reservation(
                request.namespace, request.job_id, request.resources
            )
            return ResourceReservationReply(accepted=accepted), None

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            logger.error(f"Failed to handle resource reservation request for {request.namespace}:{request.job_id}'")
            logger.error(trace)
            return ResourceReservationReply(accepted=False), None

    @staticmethod
    def request_type():
        return ResourceReservationRequest

    @staticmethod
    def response_type():
        return ResourceReservationReply


class ResourceReservationCancellation(BaseModel):
    namespace: str
    job_id: str


class P2PCancelNamespaceReservation(P2PProtocol):
    NAME = 'nodedb-namespace-cancel'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    async def perform(cls, node, peer: NodeInfo, namespace: str, job_id: str) -> None:
        # get the fully qualified P2P address for the peer
        peer_address = P2PAddress(
            address=peer.p2p_address,
            curve_secret_key=node.keystore.curve_secret_key(),
            curve_public_key=node.keystore.curve_public_key(),
            curve_server_key=peer.identity.c_public_key
        )

        try:
            # send the request
            reply, _ = await p2p_request(
                peer_address, cls.NAME, ResourceReservationCancellation(namespace=namespace, job_id=job_id)
            )

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            logger.error(f"Failed to send resource reservation cancellation for {namespace}:{job_id}' "
                         f"to: {peer.identity.name} | {peer.identity.id}")
            logger.error(trace)

    async def handle(
            self, request: ResourceReservationRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        try:
            self._node.db.handle_namespace_cancellation(request.namespace, request.job_id)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            logger.error(f"Failed to handle resource reservation cancellation for {request.namespace}:{request.job_id}")
            logger.error(trace)

        return None, None

    @staticmethod
    def request_type():
        return ResourceReservationCancellation

    @staticmethod
    def response_type():
        return None
