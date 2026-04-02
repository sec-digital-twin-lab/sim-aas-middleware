import abc
import json
import os
import threading
import traceback
import zmq

from typing import Optional, Tuple
from pydantic import BaseModel
from zmq.asyncio import Socket

from simaas.core.logging import get_logger
from simaas.core.errors import NetworkError

log = get_logger('simaas.p2p', 'p2p')

# Thread-local zmq context: one sync context (and its I/O thread) per calling
# thread, reused across requests. Prevents I/O thread churn that causes
# fork()/dlopen() deadlocks via allocator lock contention.
_thread_local = threading.local()


def _get_context() -> zmq.Context:
    """Return a zmq sync Context bound to the current thread."""
    ctx = getattr(_thread_local, 'zmq_context', None)
    if ctx is None or ctx.closed:
        ctx = zmq.Context()
        _thread_local.zmq_context = ctx
    return ctx


class P2PMessage(BaseModel):
    protocol: str
    type: str
    content: Optional[dict]
    attachment_size: int


class P2PAddress(BaseModel):
    address: str
    curve_secret_key: Optional[bytes]
    curve_public_key: Optional[bytes]
    curve_server_key: Optional[str]


class P2PProtocol(abc.ABC):
    def __init__(self, protocol: str) -> None:
        self._protocol = protocol

    def name(self) -> str:
        return self._protocol

    @abc.abstractmethod
    async def handle(
            self, request: BaseModel, attachment_path: Optional[str] = None, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        ...

    @staticmethod
    def request_type() -> BaseModel:
        ...

    @staticmethod
    def response_type() -> Optional[BaseModel]:
        ...


def _encode_message(msg: P2PMessage) -> bytes:
    return json.dumps(msg.model_dump()).encode('utf-8')


def _decode_message(data: bytes) -> P2PMessage:
    return P2PMessage.model_validate(json.loads(data.decode('utf-8')))


def p2p_request(
        peer: P2PAddress, protocol: str, content: BaseModel, reply_type: Optional[BaseModel] = None,
        attachment_path: Optional[str] = None, download_path: Optional[str] = None,
        timeout: int = 5000, chunk_size: int = 1024 * 1024
) -> Tuple[Optional[BaseModel], Optional[str]]:
    context = _get_context()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.LINGER, 1000)
    socket.setsockopt(zmq.RCVTIMEO, timeout)
    socket.setsockopt(zmq.SNDTIMEO, timeout)
    if peer.curve_secret_key and peer.curve_public_key and peer.curve_server_key:
        socket.curve_secretkey = peer.curve_secret_key
        socket.curve_publickey = peer.curve_public_key
        socket.curve_serverkey = bytes.fromhex(peer.curve_server_key)

    operation = 'connect'
    try:
        socket.connect(peer.address)

        # send request
        operation = 'send'
        rid = os.urandom(8)
        attachment_size = os.path.getsize(attachment_path) if attachment_path else 0
        request = P2PMessage(
            protocol=protocol, type='request', content=content.model_dump(),
            attachment_size=attachment_size
        )
        socket.send_multipart([rid, _encode_message(request)])

        if attachment_path is not None:
            with open(attachment_path, 'rb') as f:
                while chunk := f.read(chunk_size):
                    socket.send_multipart([rid, chunk])

        # scale receive timeout for large attachments: 30s base + 200ms per MB
        if attachment_size > 0:
            timeout = max(timeout, 30_000 + (attachment_size // (1024 * 1024)) * 200)
            socket.setsockopt(zmq.RCVTIMEO, timeout)

        # receive reply
        operation = 'receive'
        frames = socket.recv_multipart()
        reply = _decode_message(frames[1])

        if reply.attachment_size > 0:
            if download_path is None:
                download_path = os.devnull
            with open(download_path, 'wb') as f:
                total = 0
                while total < reply.attachment_size:
                    frames = socket.recv_multipart()
                    chunk = frames[-1]
                    f.write(chunk)
                    total += len(chunk)

        if reply_type is not None and reply.content is not None:
            return reply_type.model_validate(reply.content), download_path
        return None, download_path

    except NetworkError:
        raise

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        raise NetworkError(peer_address=peer.address, operation=operation, timeout_ms=timeout, trace=trace)

    finally:
        socket.close()


async def p2p_respond(
        socket: Socket, cid: bytes, rid: bytes, protocol: P2PProtocol, request: P2PMessage,
        attachment_path: Optional[str] = None, download_path: Optional[str] = None, chunk_size: int = 1024 * 1024
) -> None:
    try:
        request_type = protocol.request_type()
        reply_content, reply_attachment_path = await protocol.handle(
            request_type.model_validate(request.content), attachment_path, download_path
        )

        reply_attachment_size = os.path.getsize(reply_attachment_path) if reply_attachment_path else 0
        reply = P2PMessage(
            protocol=protocol.name(), type='reply',
            content=reply_content.model_dump() if reply_content else None,
            attachment_size=reply_attachment_size
        )
        await socket.send_multipart([cid, rid, _encode_message(reply)])

        if reply_attachment_path is not None:
            with open(reply_attachment_path, 'rb') as f:
                while chunk := f.read(chunk_size):
                    await socket.send_multipart([cid, rid, chunk])

    except Exception as e:
        log.error('respond', 'P2P handler error', protocol=protocol.name(), exc=e)
        raise

    finally:
        if attachment_path and os.path.isfile(attachment_path):
            os.remove(attachment_path)
