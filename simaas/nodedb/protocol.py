import asyncio
import traceback
from typing import Optional, List, Tuple, Dict

from pydantic import BaseModel

from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol
from simaas.p2p.exceptions import PeerUnavailableError
from simaas.nodedb.schemas import NodeInfo

logger = Logging.get('nodedb.protocol')


class NodeDBSnapshot(BaseModel):
    update_identity: Optional[List[Identity]]
    update_network: Optional[List[NodeInfo]]


class UpdateIdentityMessage(BaseModel):
    identity: Identity


class P2PUpdateIdentity(P2PProtocol):
    def __init__(self, node) -> None:
        super().__init__('nodedb-update-id', node.keystore)
        self._node = node

    async def perform(self, peer: NodeInfo) -> Identity:
        reply: UpdateIdentityMessage = await self.send_and_wait(
            peer, UpdateIdentityMessage(identity=self._keystore.identity), UpdateIdentityMessage
        )
        return reply.identity

    async def broadcast(self, peers: List[NodeInfo]) -> List[Identity]:
        result: List[Identity] = []
        for peer in peers:
            try:
                reply: UpdateIdentityMessage = await self.send_and_wait(
                    peer, UpdateIdentityMessage(identity=self._keystore.identity), UpdateIdentityMessage
                )
                result.append(reply.identity)
            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
                logger.error(f"Failed to send identity update to: {peer.identity.name} | {peer.identity.id}")
                logger.error(trace)

        return result

    async def handle(self, request: UpdateIdentityMessage) -> Tuple[UpdateIdentityMessage, Optional[str]]:
        logger.info(f"Received identity update from node: {request.identity.name} | {request.identity.id}")
        self._node.db.update_identity(request.identity)
        return UpdateIdentityMessage(identity=self._keystore.identity), None

    @staticmethod
    def request_type():
        return UpdateIdentityMessage

    @staticmethod
    def response_type():
        return UpdateIdentityMessage


class PeerUpdateMessage(BaseModel):
    origin: NodeInfo
    snapshot: NodeDBSnapshot


class P2PJoinNetwork(P2PProtocol):
    def __init__(self, node) -> None:
        super().__init__('nodedb-join', node.keystore)
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
                # create update message with a snapshot of the network, excluding nodes we already know about
                message = PeerUpdateMessage(
                    origin=self._node.db.get_node(),
                    snapshot=self._node.db.get_snapshot(exclude=list(processed.keys())),
                )

                # send update and wait for reply
                reply: PeerUpdateMessage = await self.send_and_wait(peer, message, PeerUpdateMessage)

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

                logger.debug(f"Adding peer at {peer.p2p_address} to db: {peer.identity.name} | {peer.identity.id}")

            except PeerUnavailableError:
                logger.debug(f"Peer at {peer.p2p_address} unavailable -> Removing from NodeDB.")
                self._node.db.remove_node_by_address(peer.p2p_address)

            # get all nodes in the network and add any nodes that we may not have been aware of
            for node in self._node.db.get_network():
                if node.identity.id not in processed:
                    remaining.append(node)

        return boot_node

    async def handle(self, request: PeerUpdateMessage) -> Tuple[PeerUpdateMessage, Optional[str]]:
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
    def __init__(self, node) -> None:
        super().__init__('nodedb-leave', node.keystore)
        self._node = node

    async def perform(self, blocking: bool = False) -> None:
        message = PeerLeaveMessage(origin=self._node.db.get_node())
        for peer in self._node.db.get_network():
            if peer.identity.id != message.origin.identity.id:
                if blocking:
                    await self.send_and_wait(peer, message, None)
                else:
                    asyncio.create_task(self.send_and_wait(peer, message, None))  # noqa: asyncio

    async def handle(self, request: PeerLeaveMessage) -> Tuple[None, Optional[str]]:
        self._node.db.update_identity(request.origin.identity)
        self._node.db.remove_node_by_id(request.origin.identity)

        return None, None

    @staticmethod
    def request_type():
        return PeerLeaveMessage

    @staticmethod
    def response_type():
        return None
