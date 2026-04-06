import abc
import json
import os
import random
import traceback
import zmq

from typing import Optional, Tuple
from pydantic import BaseModel
from zmq import Again
from zmq.asyncio import Socket, Context

from simaas.core.logging import get_logger
from simaas.core.errors import NetworkError

log = get_logger('simaas.p2p', 'p2p')


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


async def p2p_request(
        peer: P2PAddress, protocol: str, content: BaseModel, reply_type: Optional[BaseModel] = None,
        attachment_path: Optional[str] = None, download_path: Optional[str] = None,
        timeout: int = 5000, chunk_size: int = 1024 * 1024
) -> Tuple[Optional[BaseModel], Optional[str]]:
    # create socket
    context = Context.instance()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVTIMEO, timeout)
    socket.setsockopt(zmq.SNDTIMEO, timeout)
    if peer.curve_secret_key and peer.curve_public_key and peer.curve_server_key:
        socket.curve_secretkey = peer.curve_secret_key
        socket.curve_publickey = peer.curve_public_key
        socket.curve_serverkey = bytes.fromhex(peer.curve_server_key)

    # try to establish connection
    try:
        socket.connect(peer.address)

    except Again as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        socket.close()
        raise NetworkError(peer_address=peer.address, operation='connect', timeout_ms=timeout, trace=trace)

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        socket.close()
        raise NetworkError(peer_address=peer.address, operation='connect', trace=trace)

    # try to send the request
    try:
        # build and send request message
        rid = str(random.randint(1, 2 ** 32 - 1)).encode('utf-8')
        attachment_size = os.path.getsize(attachment_path) if attachment_path else 0
        request: P2PMessage = P2PMessage(
            protocol=protocol, type='request', content=content.model_dump(),
            attachment_size=attachment_size
        )
        request: dict = request.model_dump()
        request: str = json.dumps(request)
        request: bytes = request.encode('utf-8')
        await socket.send_multipart([rid, request])

        # send the attachment (if any)
        if attachment_path is not None:
            with open(attachment_path, 'rb') as f:
                total_sent = 0
                while total_sent < attachment_size:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    await socket.send_multipart([rid, chunk])
                    total_sent += len(chunk)

    except Again as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        socket.close()
        raise NetworkError(peer_address=peer.address, operation='send', timeout_ms=timeout, trace=trace)

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        socket.close()
        raise NetworkError(peer_address=peer.address, operation='send', trace=trace)

    # try to receive the reply
    try:
        # wait for reply and parse it
        frames = await socket.recv_multipart()
        rid, content = frames[:2]
        reply: str = content.decode('utf-8')
        reply: dict = json.loads(reply)
        reply: P2PMessage = P2PMessage.model_validate(reply)

        # receive the attachment (if any)
        if reply.attachment_size > 0:
            # what if we have an attachment but no download path? write to devnull
            if download_path is None:
                download_path = os.devnull

            # receive the attachment in chunks
            total_received = 0
            while total_received < reply.attachment_size:
                with open(download_path, 'ab') as f:
                    frames = await socket.recv_multipart()
                    rid, chunk = frames
                    f.write(chunk)
                    total_received += len(chunk)

        # are we expecting a reply?
        if reply_type is not None and reply.content is not None:
            reply: reply_type = reply_type.model_validate(reply.content)
        else:
            reply = None

        # close the socket
        socket.close()

        return reply, download_path

    except Again as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        socket.close()
        raise NetworkError(peer_address=peer.address, operation='receive', timeout_ms=timeout, trace=trace)

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        socket.close()
        raise NetworkError(peer_address=peer.address, operation='receive', trace=trace)

async def p2p_respond(
        socket: Socket, cid: bytes, rid: bytes, protocol: P2PProtocol, request: P2PMessage,
        attachment_path: Optional[str] = None, download_path: Optional[str] = None, chunk_size: int = 1024 * 1024
) -> None:
    try:
        # Handle the request - let application errors propagate with clear tracebacks
        request_type = protocol.request_type()
        reply_content, reply_attachment_path = await protocol.handle(
            request_type.model_validate(request.content), attachment_path, download_path
        )

        # some casting for PyCharm
        reply_content: Optional[BaseModel] = reply_content
        reply_attachment_path: Optional[str] = reply_attachment_path
        reply_attachment_size: int = os.path.getsize(reply_attachment_path) if reply_attachment_path else 0

        # prepare and send the reply message - wrap ZMQ ops for transport debugging
        try:
            reply: P2PMessage = P2PMessage(
                protocol=protocol.name(), type='reply',
                content=reply_content.model_dump() if reply_content else None,
                attachment_size=reply_attachment_size
            )
            reply: dict = reply.model_dump()
            reply: str = json.dumps(reply)
            reply: bytes = reply.encode('utf-8')
            await socket.send_multipart([cid, rid, reply])

            # if we have a reply attachment, send it in chunks
            if reply_attachment_path is not None:
                with open(reply_attachment_path, 'rb') as f:
                    total_sent = 0
                    while total_sent < reply_attachment_size:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break

                        await socket.send_multipart([cid, rid, chunk])
                        total_sent += len(chunk)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            log.error('respond', 'Unexpected P2P error', exc=e)
            raise NetworkError(operation='respond', trace=trace)

    finally:
        if attachment_path:
            if os.path.isfile(attachment_path):
                os.remove(attachment_path)
