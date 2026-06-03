import os.path
import random
import tempfile
import time
from typing import Optional, Tuple

from pydantic import BaseModel

from simaas.core.helpers import get_timestamp_now
from simaas.core.errors import NetworkError, OperationError
from simaas.core.identity import Identity
from simaas.core.logging import get_logger
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress

log = get_logger('simaas.p2p', 'p2p')


class LatencyMessage(BaseModel):
    t_now: int


class P2PLatency(P2PProtocol):
    NAME = 'p2p-latency'

    def __init__(self) -> None:
        super().__init__(self.NAME)

    @classmethod
    def perform(cls, p2p_address: str, peer: Identity, max_attempts: int = 10) -> Tuple[float, int]:
        peer_address = P2PAddress(address=p2p_address, peer_tls_cert=peer.tls_cert)
        for attempt in range(max_attempts):
            try:
                t0 = get_timestamp_now()
                reply, _ = p2p_request(
                    peer_address, cls.NAME, LatencyMessage(t_now=t0),
                    reply_type=LatencyMessage,
                )
                return reply.t_now - t0, attempt
            except NetworkError:
                time.sleep(0.5)

        raise OperationError(operation='latency_test', cause=f'failed after {max_attempts} attempts')

    def handle(
            self, request: LatencyMessage, attachment_path: Optional[str] = None, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        return LatencyMessage(t_now=get_timestamp_now()), None

    @staticmethod
    def request_type():
        return LatencyMessage

    @staticmethod
    def response_type():
        return LatencyMessage


class ThroughputMessage(BaseModel):
    t_now: int


class P2PThroughput(P2PProtocol):
    NAME = 'p2p-throughput'

    def __init__(self) -> None:
        super().__init__(self.NAME)

    @classmethod
    def perform(cls, p2p_address: str, peer: Identity, size: int,
                max_attempts: int = 10) -> Tuple[float, float, int]:
        peer_address = P2PAddress(address=p2p_address, peer_tls_cert=peer.tls_cert)
        with tempfile.TemporaryDirectory() as tempdir:
            attachment_path = os.path.join(tempdir, 'payload')
            with open(attachment_path, 'wb') as f:
                f.write(random.randbytes(size))

            for attempt in range(max_attempts):
                try:
                    t0 = get_timestamp_now()
                    reply, _ = p2p_request(
                        peer_address, cls.NAME, ThroughputMessage(t_now=t0),
                        reply_type=ThroughputMessage, attachment_path=attachment_path,
                    )
                    t1 = get_timestamp_now()

                    dt_upload = (reply.t_now - t0) / 1000.0
                    dt_download = (t1 - reply.t_now) / 1000.0
                    upload = (size / dt_upload) / 1024.0
                    download = (size / dt_download) / 1024.0
                    return upload, download, attempt

                except NetworkError:
                    time.sleep(0.5)

            raise OperationError(operation='throughput_test', cause=f'failed after {max_attempts} attempts')

    def handle(
            self, request: ThroughputMessage, attachment_path: Optional[str] = None, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        return ThroughputMessage(t_now=get_timestamp_now()), attachment_path

    @staticmethod
    def request_type():
        return ThroughputMessage

    @staticmethod
    def response_type():
        return ThroughputMessage
