from typing import Optional, List

from pydantic import BaseModel

from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.nodedb.exceptions import UnexpectedIdentityError
from simaas.p2p.exceptions import PeerUnavailableError
from simaas.p2p.protocol import P2PProtocol
from simaas.nodedb.schemas import NodeInfo

logger = Logging.get('nodedb.protocol')


class NodeDBSnapshot(BaseModel):
    update_identity: Optional[List[Identity]]
    update_network: Optional[List[NodeInfo]]


class UpdateMessage(BaseModel):
    origin_who: Identity
    origin_node: NodeInfo
    snapshot: NodeDBSnapshot
    reciprocate: bool


class LeaveRequest(BaseModel):
    pass


class PingRequest(BaseModel):
    identity: Identity


class NodeDBP2PProtocol(P2PProtocol):
    id = "node_db"

    def __init__(self, node) -> None:
        super().__init__(node.identity, node.datastore, NodeDBP2PProtocol.id, [
            (UpdateMessage, self._handle_update, UpdateMessage),
            (LeaveRequest, self._handle_leave, None),
            (PingRequest, self._handle_ping, PingRequest)
        ])
        self._node = node

    def ping_node(self, boot_node_address: (str, int)) -> Identity:
        response, _, peer = self.request(boot_node_address, PingRequest(identity=self._node.identity))
        return response.identity

    def perform_join(self, boot_node_address: (str, int)) -> None:
        # send an update to the boot node, then proceed to send updates to all peers that discovered along the way
        remaining = [boot_node_address]
        processed = []
        while len(remaining) > 0:
            peer_address = remaining.pop(0)
            processed.append(peer_address)

            # for join, we expect the peer to reciprocate and not to forward our message (because we will
            # contact them directly)
            try:
                response, _, peer = self.request(peer_address, UpdateMessage(
                    origin_who=self._node.identity,
                    origin_node=self._node.db.get_node(),
                    snapshot=self._node.db.get_snapshot(exclude=[self._node.identity.id]),
                    reciprocate=True
                ))
                logger.debug(f"Adding peer at {peer_address} to db: {peer.name} | {peer.id}")
                self._handle_update(response, peer)

            except PeerUnavailableError:
                logger.debug(f"Peer at {peer_address} unavailable -> Removing from NodeDB.")
                self._node.db.remove_node_by_address(peer_address)

            # get all nodes in the network and add any nodes that we may not have been aware of
            for node in self._node.db.get_network():
                if node.p2p_address not in processed and node.p2p_address not in remaining and \
                        node.p2p_address != self._node.p2p.address():
                    remaining.append(node.p2p_address)

    def perform_leave(self) -> None:
        self.broadcast(self._node.db.get_network(), LeaveRequest())
        self._node.db.reset_network()

    def _handle_update(self, request: UpdateMessage, peer: Identity) -> Optional[UpdateMessage]:
        # does the identity check out?
        if request.origin_who.id != peer.id:
            raise UnexpectedIdentityError({
                'expected': peer.dict(),
                'actual': request.origin_who.dict()
            })

        # FIXME: node will not check if the peer hostname is reachable before adding to db
        # update the db information about the originator
        self._node.db.update_identity(request.origin_who)
        self._node.db.update_network(request.origin_node)

        # process the snapshot identities (if any)
        if request.snapshot.update_identity:
            for identity in request.snapshot.update_identity:
                self._node.db.update_identity(identity)

        # process the snapshot nodes (if any)
        if request.snapshot.update_network:
            for node in request.snapshot.update_network:
                self._node.db.update_network(node)

        # reciprocate with an update message (if requested)
        return UpdateMessage(
            origin_who=self._node.identity,
            origin_node=self._node.db.get_node(),
            snapshot=self._node.db.get_snapshot(exclude=[self._node.identity.id, peer.id]),
            reciprocate=False
        ) if request.reciprocate else None

    def _handle_leave(self, _: LeaveRequest, peer: Identity) -> None:
        self._node.db.update_identity(peer)
        self._node.db.remove_node_by_id(peer)

    def _handle_ping(self, _: PingRequest, peer: Identity) -> PingRequest:
        logger.info(f"Received ping request from node: {peer.name} | {peer.id}")
        return PingRequest(identity=self._node.identity)

    def broadcast_identity_update(self, identity: Identity) -> None:
        # this is a simple update. we expect the peer to NOT reciprocate and to NOT forward our message (because we are
        # broadcasting to everyone we know)
        self.broadcast(self._node.db.get_network(), UpdateMessage(
            origin_who=self._node.identity,
            origin_node=self._node.db.get_node(),
            snapshot=NodeDBSnapshot(update_identity=[identity], update_network=None),
            reciprocate=False
        ))
