from __future__ import annotations

from typing import Optional, Dict, List, Any, Tuple

from pydantic import BaseModel

from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.exceptions import PeerUnavailableError
from simaas.p2p.messenger import SecureMessenger, P2PMessage

logger = Logging.get('p2p.protocol')


class BroadcastResponse(BaseModel):
    responses: Dict[str, Tuple[Any, Optional[str], Identity]]
    unavailable: List[NodeInfo]


class P2PProtocol:
    """
    P2PProtocol is the base class for all P2P protocol classes. It provides convenience methods that are
    needed regardless of the specific protocol implementation.
    """

    def __init__(self, identity: Identity, datastore: str, protocol_name: str, mapping: list[tuple]):
        # self._node = node
        self._identity = identity
        self._datastore = datastore
        self._protocol_name = protocol_name
        self._function_mapping = {item[0].__name__: item for item in mapping}
        self._seq_id_counter = 0

    def _next_seq_id(self) -> int:
        self._seq_id_counter += 1
        return self._seq_id_counter

    # @property
    # def node(self):
    #     return self._node

    @property
    def name(self) -> str:
        return self._protocol_name

    def supports(self, message_type: str) -> bool:
        return message_type in self._function_mapping

    def handle_message(self, message: P2PMessage, peer: Identity) -> Optional[P2PMessage]:
        """
        Handles a message that has been received by forwarding it to the appropriate handler function for this
        type of message.
        :param message: the message from the peer
        :param peer: the identity of the peer that sent the message
        :return: the response to be sent back to the peer (if any - None if not)
        """
        req_class, function, resp_class = self._function_mapping[message.type]
        response = function(req_class.parse_obj(message.content), peer)
        if response is None:
            content = None
            attachment = None

        else:
            if isinstance(response, tuple):
                content = response[0]
                attachment = response[1]

            else:
                content = response
                attachment = None

        return P2PMessage(protocol=self._protocol_name,
                          type=resp_class.__name__,
                          content=content.dict() if content else None,
                          attachment=attachment,
                          sequence_id=message.sequence_id) if resp_class else None

    def request(self, address: (str, int), request: BaseModel,
                attachment: str = None) -> (Optional[BaseModel], str, Identity):

        # convert the request into the outgoing message
        request_type = type(request).__name__
        message_out = P2PMessage(protocol=self._protocol_name, type=request_type, content=request.dict(),
                                 attachment=attachment, sequence_id=self._next_seq_id())

        # send the message...
        peer, messenger = SecureMessenger.connect(address, self._identity, self._datastore)
        logger.debug(f"[req:{message_out.sequence_id:06d}] ({self._identity.id[:8]}) -> ({peer.id[:8]}) "
                     f"{message_out.protocol} {message_out.type} {message_out.attachment is not None}")

        # ...and wait for the response
        message_in = messenger.send_message(message_out)
        if message_in:
            messenger.close()
            logger.debug(f"[res:{message_in.sequence_id:06d}] ({self._identity.id[:8]}) <- ({peer.id[:8]})")

            # convert the incoming message in the response
            _, _, resp_class = self._function_mapping[request_type]
            response = resp_class.parse_obj(message_in.content) if message_in.content else None
            return response, message_in.attachment, peer

        else:
            messenger.close()
            return None, None, peer

    def broadcast(self, network: List[NodeInfo], request: BaseModel, attachment: str = None, exclude: list[str] = None,
                  exclude_self: bool = True) -> Optional[BroadcastResponse]:

        # convert the request into the outgoing message
        request_type = type(request).__name__
        message_out = P2PMessage(protocol=self._protocol_name, type=request_type, content=request.dict(),
                                 attachment=attachment, sequence_id=self._next_seq_id())

        # determine the exclude list
        exclude = exclude if exclude else []
        if exclude_self:
            exclude.append(self._identity.id)

        # send requests to all peers we know of and collect the responses
        responses = {}
        unavailable = []
        for node in network:
            # is this peer iid in the exclusion list?
            if node.identity.id in exclude:
                continue

            # connect to the peer (if it is online), send a request and keep the response. if a peer is not available,
            # we just skip it (as this is a broadcast we can't expect every peer in the list to be online/reachable).
            try:
                # send the message...
                peer, messenger = SecureMessenger.connect(node.p2p_address, self._identity, self._datastore)
                logger.debug(f"[Breq:{message_out.sequence_id:06d}] ({self._identity.id[:8]}) -> ({peer.id[:8]}) "
                             f"{message_out.protocol} {message_out.type} {message_out.attachment is not None}")

                # ...and wait for the response
                message_in = messenger.send_message(message_out)
                if message_in:
                    logger.debug(f"[Bres:{message_in.sequence_id:06d}] "
                                 f"({self._identity.id[:8]}) <- ({peer.id[:8]})")

                    # convert the incoming message in the response and keep it
                    _, _, resp_class = self._function_mapping[request_type]
                    response = resp_class.parse_obj(message_in.content) if message_in.content and resp_class else None
                    responses[peer.id] = (response, message_in.attachment, peer)

                else:
                    responses[peer.id] = (None, None, peer)

                messenger.close()

            except PeerUnavailableError:
                unavailable.append(node)

        return BroadcastResponse(responses=responses, unavailable=unavailable)
