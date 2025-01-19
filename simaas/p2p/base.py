import abc
import json
import os
import tempfile
import traceback
import zmq

from typing import Optional, List, Tuple
from pydantic import BaseModel
from zmq import Again
from zmq.asyncio import Socket, Context

from simaas.core.logging import Logging
from simaas.p2p.exceptions import PeerUnavailableError, UnexpectedP2PError

logger = Logging.get('p2p')


class P2PAttachment(BaseModel):
    name: str
    path: Optional[str]


class P2PMessage(BaseModel):
    protocol: str
    type: str
    content: dict
    attachment: Optional[str]


class P2PAddress(BaseModel):
    address: str
    curve_secret_key: Optional[bytes]
    curve_public_key: Optional[bytes]
    curve_server_key: Optional[str]


async def p2p_request(
        peer: P2PAddress, protocol: str, content: BaseModel, reply_type: Optional[BaseModel] = None,
        attachment: Optional[P2PAttachment] = None, download_path: Optional[str] = None, timeout: int = 500
) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
    try:
        # create socket connection
        ctx = Context()
        socket = ctx.socket(zmq.DEALER)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, timeout)
        socket.setsockopt(zmq.SNDTIMEO, timeout)
        if peer.curve_secret_key and peer.curve_public_key and peer.curve_server_key:
            socket.curve_secretkey = peer.curve_secret_key
            socket.curve_publickey = peer.curve_public_key
            socket.curve_serverkey = bytes.fromhex(peer.curve_server_key)
        socket.connect(peer.address)

        # send request message
        request: P2PMessage = P2PMessage(
            protocol=protocol, type='request', content=content.model_dump(),
            attachment=attachment.name if attachment else None
        )
        request: dict = request.model_dump()
        request: str = json.dumps(request)
        request: bytes = request.encode('utf-8')
        await socket.send_multipart([request])

        # send the attachment (if any)
        if attachment is not None:
            with open(attachment.path, 'rb') as f:
                chunk_size = 1024 * 1024
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    await socket.send_multipart([chunk])

                await socket.send_multipart([b'EOF'])

        # are we expecting a reply?
        if reply_type is not None:
            # wait for reply
            reply: List[bytes] = await socket.recv_multipart()
            reply: bytes = reply[0]
            reply: str = reply.decode('utf-8')
            reply: dict = json.loads(reply)
            reply: P2PMessage = P2PMessage.model_validate(reply)

            # receive the attachment (if any)
            attachment: Optional[P2PAttachment] = None
            if reply.attachment is not None:
                # what if we have an attachment but no download path? write to devnull
                if download_path is None:
                    path = os.devnull

                else:
                    path = os.path.join(download_path, reply.attachment) if os.path.isdir(download_path) else download_path
                    attachment = P2PAttachment(name=reply.attachment, path=path)

                # receive the attachment in chunks
                with open(path, 'wb') as f:
                    while True:
                        msg: List[bytes] = await socket.recv_multipart()
                        chunk = msg[0]
                        if chunk == b'EOF':
                            break
                        f.write(chunk)

            reply: reply_type = reply_type.model_validate(reply.content)

        else:
            await socket.recv_multipart()
            attachment = None
            reply = None

        # close the socket
        socket.close()

        return reply, attachment

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


class P2PProtocol(abc.ABC):
    def __init__(self, protocol: str) -> None:
        self._protocol = protocol

    def name(self) -> str:
        return self._protocol

    async def process_and_reply(self, socket: Socket, cid: bytes, request: P2PMessage) -> None:
        try:
            request_type = self.request_type()

            # do we have an attachment?
            if request.attachment:
                with tempfile.TemporaryDirectory() as temp_dir:
                    # receive the attachment in chunks
                    request_attachment_path: str = os.path.join(temp_dir, request.attachment)
                    with open(request_attachment_path, 'wb') as f:
                        while True:
                            msg: List[bytes] = await socket.recv_multipart()
                            chunk = msg[0]
                            if chunk == b'EOF':
                                break
                            f.write(chunk)

                    reply_content, reply_attachment = await self.handle(
                        request_type.model_validate(request.content), request_attachment_path
                    )

            else:
                reply_content, reply_attachment = await self.handle(
                    request_type.model_validate(request.content)
                )

            # some casting for PyCharm
            reply_content: Optional[BaseModel] = reply_content
            reply_attachment: Optional[P2PAttachment] = reply_attachment

            # are we sending a reply at all?
            if reply_content is not None:
                # prepare and send the reply message
                reply: P2PMessage = P2PMessage(
                    protocol=self._protocol, type='reply', content=reply_content.model_dump(),
                    attachment=reply_attachment.name if reply_attachment else None
                )
                reply: dict = reply.model_dump()
                reply: str = json.dumps(reply)
                reply: bytes = reply.encode('utf-8')
                await socket.send_multipart([cid, reply])

                # if we have a reply attachment, send it in chunks
                if reply_attachment is not None:
                    with open(reply_attachment.path, 'rb') as f:
                        chunk_size = 1024 * 1024
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break

                            await socket.send_multipart([cid, chunk])

                        await socket.send_multipart([cid, b'EOF'])

            else:
                await socket.send_multipart([cid, b''])

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise UnexpectedP2PError(details={
                'trace': trace
            })

    @abc.abstractmethod
    async def handle(
            self, request: BaseModel, attachment_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
        ...

    @staticmethod
    def request_type() -> BaseModel:
        ...

    @staticmethod
    def response_type() -> Optional[BaseModel]:
        ...
