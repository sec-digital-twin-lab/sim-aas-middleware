import asyncio
import json
import os
import socket
import tempfile
import threading
import traceback

import zmq

from threading import Lock
from typing import Optional, Dict, Tuple

from zmq import Again
from zmq.asyncio import Socket, Context

from simaas.core.errors import ConfigurationError, OperationError
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol, P2PMessage, p2p_respond

logger = Logging.get('p2p')


class P2PService:
    def __init__(self, keystore: Keystore, address: str) -> None:
        self._mutex = Lock()
        self._keystore = keystore
        self._address = address
        self._port = int(address.split(':')[-1])
        self._socket: Optional[Socket] = None
        self._protocols: Dict[str, P2PProtocol] = {}
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._pending: Dict[P2PProtocol, bytes, Tuple[P2PMessage, str]] = {}
        self._thread: Optional[threading.Thread] = None

    def is_ready(self) -> bool:
        return self._socket is not None

    def add(self, protocol: P2PProtocol) -> None:
        """Adds a protocol to the P2P service."""
        logger.info(f"add P2P protocol: {protocol.name()}")
        self._protocols[protocol.name()] = protocol

    def address(self) -> str:
        """Returns the address (host:port) of the P2P service."""
        return self._address

    def port(self) -> int:
        """Returns the port of the P2P service."""
        return self._port

    def fq_address(self) -> str:
        """Returns the fully qualified address of the P2P service"""
        fqdn = socket.getfqdn()
        return f"tcp://{fqdn}:{self._port}"

    def _init_socket(self, encrypt: bool = True, timeout: int = 5000) -> None:
        """Initialize the ZMQ socket."""
        if not self._socket:
            try:
                context = Context.instance()
                self._socket = context.socket(zmq.ROUTER)
                self._socket.setsockopt(zmq.LINGER, 0)
                self._socket.setsockopt(zmq.RCVTIMEO, timeout)
                self._socket.setsockopt(zmq.SNDTIMEO, timeout)
                if encrypt:
                    self._socket.curve_secretkey = self._keystore.curve_secret_key()
                    self._socket.curve_publickey = self._keystore.curve_public_key()
                    self._socket.curve_server = True
                self._socket.bind(self._address)
                self._ready_event.set()
                logger.info(f"[{self._keystore.identity.name}] P2P server initialised at '{self._address}'")
            except Exception as e:
                raise ConfigurationError(
                    path='p2p.socket',
                    expected='socket binding',
                    actual=str(e),
                    hint='P2P server socket cannot be created'
                )

    async def start_service(self, encrypt: bool = True, timeout: int = 5000) -> asyncio.Task:
        """Starts the P2P server and returns the connection handler task.

        Use this in async contexts where the event loop will keep running.
        """
        with self._mutex:
            self._init_socket(encrypt, timeout)
        return asyncio.create_task(self._handle_incoming_connections())

    def start_service_background(self, encrypt: bool = True, timeout: int = 5000) -> None:
        """Starts the P2P server in a background thread with its own event loop.

        Use this in sync contexts (like DefaultNode.create()) where the caller
        won't maintain a running event loop.
        """
        # Store config for socket initialization in the background thread
        self._bg_encrypt = encrypt
        self._bg_timeout = timeout
        self._bg_error = None
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        # Wait for socket to be ready or error using event (outside mutex to avoid deadlock)
        if not self._ready_event.wait(timeout=10.0):
            if self._bg_error is not None:
                raise self._bg_error
            raise OperationError(
                operation='p2p_start',
                stage='initialization',
                cause='timeout',
                hint='P2P service failed to start within timeout'
            )

    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Initialize socket in this event loop where it will be used
            with self._mutex:
                self._init_socket(self._bg_encrypt, self._bg_timeout)
            loop.run_until_complete(self._handle_incoming_connections())
        except Exception as e:
            self._bg_error = e
        finally:
            loop.close()

    def stop_service(self) -> None:
        """Signals the server to stop accepting connections."""
        logger.info(f"[{self._keystore.identity.name}] initiating shutdown of P2P service.")
        self._stop_event.set()
        self._ready_event.clear()
        # Wait for the thread to finish (with timeout to avoid hanging)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    async def wait_until_ready(self, timeout: float = 10.0) -> bool:
        """Wait until the P2P service is ready.

        Returns True if service is ready, False if timeout occurred.
        """
        start = asyncio.get_event_loop().time()
        while not self._ready_event.is_set():
            if asyncio.get_event_loop().time() - start > timeout:
                return False
            await asyncio.sleep(0.1)
        return True

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
