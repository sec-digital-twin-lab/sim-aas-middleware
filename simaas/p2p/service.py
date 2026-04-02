import asyncio
import json
import os
import socket
import tempfile
import threading

import zmq

from threading import Lock
from typing import Optional, Dict, Set, Tuple

from zmq.asyncio import Socket, Context

from simaas.core.errors import ConfigurationError, OperationError
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.p2p.base import P2PProtocol, P2PMessage, p2p_respond

log = get_logger('simaas.p2p', 'p2p')


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
        self._stopped_event = threading.Event()
        self._pending: Dict[Tuple[bytes, bytes], Tuple[P2PProtocol, P2PMessage, str, int]] = {}
        self._handler_tasks: Set[asyncio.Task] = set()
        self._thread: Optional[threading.Thread] = None

    def is_ready(self) -> bool:
        return self._socket is not None

    def add(self, protocol: P2PProtocol) -> None:
        """Adds a protocol to the P2P service."""
        log.info('protocol', 'Adding P2P protocol', name=protocol.name())
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
                self._socket.setsockopt(zmq.SNDTIMEO, timeout)
                if encrypt:
                    self._socket.curve_secretkey = self._keystore.curve_secret_key()
                    self._socket.curve_publickey = self._keystore.curve_public_key()
                    self._socket.curve_server = True
                self._socket.bind(self._address)
                self._ready_event.set()
                log.info('server', 'P2P server initialised', address=self._address)
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
        # Clear events for clean start
        self._stop_event.clear()
        self._stopped_event.clear()
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
        # Clear events for clean start
        self._stop_event.clear()
        self._stopped_event.clear()
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
        """Signals the server to stop accepting connections and waits for cleanup."""
        log.info('server', 'Initiating P2P service shutdown')
        self._stop_event.set()
        self._ready_event.clear()

        # Wait for the handler to signal it has stopped (socket closed)
        if not self._stopped_event.wait(timeout=5.0):
            log.warning('server', 'Handler did not stop cleanly, forcing socket close')
            # Force close the socket from this thread as fallback
            with self._mutex:
                if self._socket is not None:
                    try:
                        self._socket.close()
                        self._socket = None
                    except Exception as e:
                        log.warning('server', 'Error force-closing socket', exc=e)

        # Wait for the thread to finish
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                log.warning('server', 'P2P thread did not exit cleanly')

        log.info('server', 'P2P service shutdown complete')

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

    def _dispatch_handler(self, cid: bytes, rid: bytes, protocol: P2PProtocol,
                          request: P2PMessage, attachment_path: Optional[str],
                          download_path: str) -> None:
        """Dispatch a request handler as a concurrent task."""
        async def _run():
            try:
                await p2p_respond(self._socket, cid, rid, protocol, request, attachment_path, download_path)
            except Exception as e:
                log.warning('server', 'Handler failed', protocol=protocol.name(), exc=e)

        task = asyncio.create_task(_run())
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _handle_incoming_connections(self):
        log.info('server', 'Listening for P2P connections')
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                while not self._stop_event.is_set():
                    try:
                        # Use asyncio.wait_for so we can check _stop_event periodically.
                        # Plain await recv_multipart() would block until a message arrives,
                        # preventing clean shutdown.
                        frames = await asyncio.wait_for(
                            self._socket.recv_multipart(), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        log.warning('server', 'Recv error', exc=e)
                        continue

                    try:
                        cid, rid, content = frames[:3]
                        pending_key = (cid, rid)

                        # Is this a continuation of a pending chunked transfer?
                        pending = self._pending.get(pending_key)

                        if pending is None:
                            # New request — parse the message
                            request: P2PMessage = P2PMessage.model_validate(
                                json.loads(content.decode('utf-8'))
                            )

                            protocol = self._protocols.get(request.protocol)
                            if protocol is None:
                                log.warning('server', 'Unsupported protocol, ignoring',
                                            protocol=request.protocol)
                                continue

                            if request.attachment_size > 0:
                                attachment_path = os.path.join(tempdir, rid.hex())
                                self._pending[pending_key] = (protocol, request, attachment_path, 0)
                            else:
                                self._dispatch_handler(cid, rid, protocol, request, None, tempdir)

                        else:
                            protocol, request, attachment_path, received = pending
                            with open(attachment_path, 'ab') as f:
                                f.write(content)
                            received += len(content)

                            if received >= request.attachment_size:
                                self._pending.pop(pending_key)
                                self._dispatch_handler(
                                    cid, rid, protocol, request, attachment_path, tempdir
                                )
                            else:
                                self._pending[pending_key] = (protocol, request, attachment_path, received)

                    except Exception as e:
                        log.warning('server', 'Error processing frame', exc=e)

                # Shutdown: wait for in-flight handlers to finish
                if self._handler_tasks:
                    log.info('server', 'Waiting for in-flight handlers',
                             count=len(self._handler_tasks))
                    await asyncio.gather(*self._handler_tasks, return_exceptions=True)

        finally:
            log.info('server', 'Stopped listening for P2P connections')
            with self._mutex:
                if self._socket is not None:
                    try:
                        self._socket.close()
                        self._socket = None
                    except Exception as e:
                        log.warning('server', 'Error closing socket', exc=e)
            self._stopped_event.set()
