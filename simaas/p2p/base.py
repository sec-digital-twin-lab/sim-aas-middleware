import abc
import json
import traceback
import zmq

from typing import Optional, List, Any, Tuple
from pydantic import BaseModel
from zmq import Again
from zmq.asyncio import Socket, Context
from zmq.utils import z85

from simaas.core.eckeypair import ECKeyPair
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.exceptions import PeerUnavailableError, UnexpectedP2PError

logger = Logging.get('p2p')


class P2PMessage(BaseModel):
    protocol: str
    type: str
    content: dict
    has_stream: bool


class P2PProtocol(abc.ABC):
    def __init__(self, protocol: str, keystore: Keystore) -> None:
        self._protocol = protocol
        self._keystore: Keystore = keystore

    def name(self) -> str:
        return self._protocol

    def connect(self, peer: NodeInfo, timeout: int = 5000) -> Socket:
        ctx = Context()
        socket = ctx.socket(zmq.DEALER)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, timeout)
        socket.setsockopt(zmq.SNDTIMEO, timeout)
        socket.curve_secretkey = self._keystore.curve_secret_key()
        socket.curve_publickey = self._keystore.curve_public_key()
        socket.curve_serverkey = bytes.fromhex(peer.identity.c_public_key)
        socket.connect(peer.p2p_address)
        return socket

    async def send_and_wait(self, peer: NodeInfo, content: BaseModel, reply_type: Optional[Any],
                            download_path: str = None) -> Any:
        try:
            socket = self.connect(peer)

            request: P2PMessage = P2PMessage(protocol=self._protocol, type='request', content=content.dict(),
                                             has_stream=False)
            request: dict = request.dict()
            request: str = json.dumps(request)
            request: bytes = request.encode('utf-8')

            await socket.send_multipart([request])

            reply: List[bytes] = await socket.recv_multipart()
            reply: bytes = reply[0]
            reply: str = reply.decode('utf-8')
            reply: dict = json.loads(reply)
            reply: P2PMessage = P2PMessage.model_validate(reply)
            has_stream: bool = reply.has_stream

            if reply_type is None:
                socket.close()
                return None

            reply: reply_type = reply_type.model_validate(reply.content)

            if download_path and has_stream:
                # receive the file in chunks
                with open(download_path, 'wb') as f:
                    while True:
                        msg: List[bytes] = await socket.recv_multipart()
                        chunk = msg[0]
                        if chunk == b'EOF':
                            break
                        f.write(chunk)

            socket.close()
            return reply

        except Again as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise PeerUnavailableError(details={
                'trace': trace
            })

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise UnexpectedP2PError(details={
                'trace': trace
            })

    async def process_and_reply(self, socket: Socket, cid: bytes, request: P2PMessage) -> None:
        try:
            request_type = self.request_type()
            request: request_type = request_type.model_validate(request.content)

            content, path = await self.handle(request)
            content: Optional[BaseModel] = content
            content: dict = content.dict() if content else {}

            reply: P2PMessage = P2PMessage(protocol=self._protocol, type='reply', content=content,
                                           has_stream=path is not None)
            reply: dict = reply.dict()
            reply: str = json.dumps(reply)
            reply: bytes = reply.encode('utf-8')

            await socket.send_multipart([cid, reply])

            if path is not None:
                with open(path, 'rb') as f:
                    chunk_size = 1024 * 1024
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break

                        await socket.send_multipart([cid, chunk])

                    await socket.send_multipart([cid, b'EOF'])

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise UnexpectedP2PError(details={
                'trace': trace
            })

    @abc.abstractmethod
    async def handle(self, request: Any) -> Tuple[Optional[BaseModel], Optional[str]]:
        ...

    @staticmethod
    def request_type() -> Any:
        ...

    @staticmethod
    def response_type() -> Optional[Any]:
        ...
