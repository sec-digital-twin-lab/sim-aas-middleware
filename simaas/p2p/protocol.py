import asyncio
from typing import Optional, Tuple

from pydantic import BaseModel

from simaas.core.helpers import get_timestamp_now
from simaas.p2p.exceptions import PeerUnavailableError
from simaas.rti.exceptions import RTIException
from simaas.core.keystore import Keystore
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress, P2PAttachment

logger = Logging.get('p2p.protocol')


class LatencyMessage(BaseModel):
    t_now: int


class P2PLatency(P2PProtocol):
    NAME = 'p2p-latency'

    def __init__(self) -> None:
        super().__init__(P2PLatency.NAME)

    @classmethod
    async def perform_unsecured(cls, p2p_address: str, max_attempts: int = 10) -> Tuple[float, int]:
        peer_address = P2PAddress(
            address=p2p_address,
            curve_secret_key=None,
            curve_public_key=None,
            curve_server_key=None
        )
        return await cls.perform(peer_address, max_attempts=max_attempts)

    @classmethod
    async def perform_secured(cls, p2p_address: str, keystore: Keystore, peer: Identity,
                              max_attempts: int = 10) -> Tuple[float, int]:
        peer_address = P2PAddress(
            address=p2p_address,
            curve_secret_key=keystore.curve_secret_key(),
            curve_public_key=keystore.curve_public_key(),
            curve_server_key=peer.c_public_key
        )
        return await cls.perform(peer_address, max_attempts=max_attempts)

    @classmethod
    async def perform(cls, peer_address: P2PAddress, max_attempts: int = 10) -> Tuple[float, int]:
        for attempt in range(max_attempts):
            try:
                t0 = get_timestamp_now()
                reply: Tuple[Optional[LatencyMessage], Optional[P2PAttachment]] = await p2p_request(
                    peer_address, cls.NAME, LatencyMessage(t_now=t0),
                    reply_type=LatencyMessage
                )
                reply: LatencyMessage = reply[0]

                latency = reply.t_now - t0
                return latency, attempt

            except PeerUnavailableError:
                await asyncio.sleep(0.5)

        raise RTIException(f"Latency test failed after {max_attempts} attempts.")

    async def handle(
            self, request: LatencyMessage, _: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
        return LatencyMessage(t_now=get_timestamp_now()), None

    @staticmethod
    def request_type():
        return LatencyMessage

    @staticmethod
    def response_type():
        return LatencyMessage
