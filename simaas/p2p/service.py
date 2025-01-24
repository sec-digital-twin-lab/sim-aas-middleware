import json
import os
import tempfile
import threading
import asyncio
import traceback

import zmq

from threading import Lock
from typing import Optional, Dict, Tuple

from zmq import Again
from zmq.asyncio import Socket, Context

from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol, P2PMessage, p2p_respond

logger = Logging.get('p2p')


class P2PService:
    def __init__(self, keystore: Keystore, address: str) -> None:
        self._mutex = Lock()
        self._keystore = keystore
        self._address = address
        self._socket: Optional[Socket] = None
        self._protocols: Dict[str, P2PProtocol] = {}
        self._stop_event = threading.Event()
        self._pending: Dict[P2PProtocol, bytes, Tuple[P2PMessage, str]] = {}

    def is_ready(self) -> bool:
        return self._socket is not None

    def add(self, protocol: P2PProtocol) -> None:
        """Adds a protocol to the P2P service."""
        logger.info(f"add P2P protocol: {protocol.name()}")
        self._protocols[protocol.name()] = protocol

    def address(self) -> str:
        """Returns the address (host:port) of the P2P service."""
        return self._address

    def start_service(self, encrypt: bool = True, timeout: int = 5000) -> None:
        """Starts the TCP socket server at the specified address."""
        with self._mutex:
            if not self._socket:
                try:
                    server_ctx = Context()
                    self._socket = server_ctx.socket(zmq.ROUTER)
                    self._socket.setsockopt(zmq.LINGER, 0)
                    self._socket.setsockopt(zmq.RCVTIMEO, timeout)
                    self._socket.setsockopt(zmq.SNDTIMEO, timeout)
                    if encrypt:
                        self._socket.curve_secretkey = self._keystore.curve_secret_key()
                        self._socket.curve_publickey = self._keystore.curve_public_key()
                        self._socket.curve_server = True
                    self._socket.bind(self._address)
                    logger.info(f"[{self._keystore.identity.name}] P2P server initialised at '{self._address}'")
                except Exception as e:
                    raise SaaSRuntimeException("P2P server socket cannot be created", details={'exception': e})

                threading.Thread(target=self._run_event_loop, daemon=True).start()

    def stop_service(self) -> None:
        """Signals the server thread to stop accepting connections."""
        logger.info(f"[{self._keystore.identity.name}] initiating shutdown of P2P service.")
        self._stop_event.set()

    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._handle_incoming_connections())
        finally:
            loop.close()

    async def _handle_incoming_connections(self):
        logger.info(f"[{self._keystore.identity.name}] listening to incoming P2P connections...")
        with tempfile.TemporaryDirectory() as tempdir:
            while not self._stop_event.is_set():
                try:
                    frames = await self._socket.recv_multipart(flags=zmq.NOBLOCK)
                    cid, rid, content = frames[:3]

                    # do we have already a pending transfer?
                    pending: Optional[Tuple[P2PProtocol, P2PMessage, str]] = self._pending.get(rid)

                    # if not, then treat it as a P2P message frame
                    if pending is None:
                        request: str = content.decode('utf-8')
                        request: dict = json.loads(request)
                        request: P2PMessage = P2PMessage.model_validate(request)

                        # do we know the protocol?
                        protocol = self._protocols.get(request.protocol)
                        if protocol is None:
                            logger.warning(
                                f"[{self._keystore.identity.name}] unsupported protocol: {request.protocol} -> ignoring."
                            )
                            continue

                        # does this request come with an attachment?
                        if request.attachment_size > 0:
                            attachment_path: str = os.path.join(tempdir, rid.decode('utf-8'))
                            self._pending[rid] = (protocol, request, attachment_path)

                        # if there is no attachment, process the message straight away
                        else:
                            await p2p_respond(
                                self._socket, cid, rid, protocol, request, download_path=tempdir
                            )

                    # if it's pending, then we need to keep receiving the contents until the attachment has been
                    # fully received.
                    else:
                        protocol: P2PProtocol = pending[0]
                        request: P2PMessage = pending[1]
                        attachment_path: str = pending[2]
                        with open(attachment_path, 'ab') as f:
                            f.write(content)

                        # is the file size equal to the size of the attachment?
                        file_size = os.path.getsize(attachment_path)
                        if file_size == request.attachment_size:
                            self._pending.pop(rid)
                            await p2p_respond(
                                self._socket, cid, rid, protocol, request, attachment_path, download_path=tempdir
                            )

                        elif file_size > request.attachment_size:
                            logger.warning(
                                f"[{self._keystore.identity.name}] attachment bigger than expected: "
                                f"{request.protocol}:{cid} -> ignoring."
                            )

                except Again:
                    await asyncio.sleep(0.1)

                except Exception as e:
                    trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                    logger.warning(f"[{self._keystore.identity.name}] Exception in server loop: {trace}")

        logger.info(f"[{self._keystore.identity.name}] Stopped listening to incoming P2P connections.")
        self._socket.close()
