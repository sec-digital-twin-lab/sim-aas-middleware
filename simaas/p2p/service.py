import json
import threading
import asyncio
import zmq

from threading import Lock
from typing import Optional, Dict

from zmq import Again
from zmq.asyncio import Socket, Context

from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol, P2PMessage

logger = Logging.get('p2p')


class P2PService:
    def __init__(self, keystore: Keystore, address: str) -> None:
        self._mutex = Lock()
        self._keystore = keystore
        self._address = address
        self._socket: Optional[Socket] = None
        self._protocols: Dict[str, P2PProtocol] = {}
        self._stop_event = threading.Event()

    def is_ready(self) -> bool:
        return self._socket is not None

    def add(self, protocol: P2PProtocol) -> None:
        """Adds a protocol to the P2P service."""
        logger.info(f"add P2P protocol: {protocol.name()}")
        self._protocols[protocol.name()] = protocol

    def address(self) -> str:
        """Returns the address (host:port) of the P2P service."""
        return self._address

    def start_service(self, timeout: int = 500) -> None:
        """Starts the TCP socket server at the specified address."""
        with self._mutex:
            if not self._socket:
                try:
                    server_ctx = Context()
                    self._socket = server_ctx.socket(zmq.ROUTER)
                    self._socket.setsockopt(zmq.LINGER, 0)
                    self._socket.setsockopt(zmq.RCVTIMEO, timeout)
                    self._socket.setsockopt(zmq.SNDTIMEO, timeout)
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
        while not self._stop_event.is_set():
            try:
                cid, message = await self._socket.recv_multipart(flags=zmq.NOBLOCK)
                await self._process_message(cid, message)

            except Again:
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning(f"[{self._keystore.identity.name}] Exception in server loop: {e}")

        logger.info(f"[{self._keystore.identity.name}] Stopped listening to incoming P2P connections.")
        self._socket.close()

    async def _process_message(self, cid: bytes, message: bytes) -> None:
        try:
            request_str = message.decode('utf-8')
            request_dict = json.loads(request_str)
            request = P2PMessage.model_validate(request_dict)

            protocol = self._protocols.get(request.protocol)
            if protocol is None:
                logger.warning(
                    f"[{self._keystore.identity.name}] unsupported protocol: {request.protocol} -> ignoring.")
                return

            await protocol.process_and_reply(self._socket, cid, request)
        except Exception as e:
            logger.error(f"[{self._keystore.identity.name}] failed to process message: {e}")
