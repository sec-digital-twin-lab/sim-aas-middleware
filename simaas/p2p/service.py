import asyncio
import os
import socket
import ssl
import struct
import tempfile
import threading
from typing import Optional, Dict, Tuple

from simaas.core.errors import ConfigurationError, OperationError
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.p2p.base import (
    P2PProtocol, P2PMessage, build_server_ssl_context,
    p2p_respond, p2p_send_error,
)

log = get_logger('simaas.p2p', 'p2p')

_CHUNK_SIZE = 1024 * 1024


class P2PService:
    def __init__(self, keystore: Keystore, address: str) -> None:
        self._keystore = keystore
        self._address = address
        self._port = int(address.split(':')[-1])
        self._protocols: Dict[str, P2PProtocol] = {}
        self._server: Optional[asyncio.base_events.Server] = None
        self._ready_event = threading.Event()
        self._stopped_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def is_ready(self) -> bool:
        return self._server is not None and self._server.is_serving()

    def add(self, protocol: P2PProtocol) -> None:
        log.info('protocol', 'Adding P2P protocol', name=protocol.name())
        self._protocols[protocol.name()] = protocol

    def address(self) -> str:
        return self._address

    def port(self) -> int:
        return self._port

    def fq_address(self) -> str:
        fqdn = socket.getfqdn()
        return f"tcp://{fqdn}:{self._port}"

    def start_service_background(self) -> None:
        self._ready_event.clear()
        self._stopped_event.clear()
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=10.0):
            raise OperationError(
                operation='p2p_start', stage='initialization', cause='timeout',
                hint='P2P service failed to start within timeout',
            )

    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except Exception as e:
            log.error('server', 'P2P loop crashed', exc=e)
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._stopped_event.set()

    async def _serve(self) -> None:
        host, port = self._parse_addr()
        try:
            ssl_ctx = build_server_ssl_context(
                self._keystore.tls_cert_pem(),
                self._keystore.tls_key_pem(),
            )
            self._server = await asyncio.start_server(self._handle_connection, host, port, ssl=ssl_ctx)
        except Exception as e:
            raise ConfigurationError(
                path='p2p.socket', expected='socket binding', actual=str(e),
                hint='P2P server cannot be created',
            )
        log.info('server', 'P2P server listening', address=self._address)
        self._ready_event.set()
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            log.info('server', 'P2P server shutting down')

    def _parse_addr(self) -> Tuple[str, int]:
        addr = self._address
        if addr.startswith('tcp://'):
            addr = addr[len('tcp://'):]
        host, port = addr.rsplit(':', 1)
        return host, int(port)

    def stop_service(self) -> None:
        if self._stopped_event.is_set():
            return
        log.info('server', 'Initiating P2P service shutdown')
        if self._server is not None and self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._server.close)
        if not self._stopped_event.wait(timeout=5.0):
            log.warning('server', 'P2P service did not stop cleanly')
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        log.info('server', 'P2P service shutdown complete')

    async def wait_until_ready(self, timeout: float = 10.0) -> bool:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while not self._ready_event.is_set():
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw_len = await reader.readexactly(4)
            (header_len,) = struct.unpack('>I', raw_len)
            header = await reader.readexactly(header_len)
            request = P2PMessage.model_validate_json(header)

            protocol = self._protocols.get(request.protocol)
            if protocol is None:
                log.warning('server', 'Unsupported protocol', protocol=request.protocol)
                await p2p_send_error(writer, request.protocol,
                                     f"protocol '{request.protocol}' is not supported by this peer")
                return

            with tempfile.TemporaryDirectory() as tempdir:
                attachment_path: Optional[str] = None
                if request.attachment_size > 0:
                    attachment_path = os.path.join(tempdir, 'attachment')
                    remaining = request.attachment_size
                    with open(attachment_path, 'wb') as f:
                        while remaining > 0:
                            chunk = await reader.read(min(_CHUNK_SIZE, remaining))
                            if not chunk:
                                log.warning('server', 'Attachment truncated', protocol=request.protocol)
                                return
                            f.write(chunk)
                            remaining -= len(chunk)

                await p2p_respond(writer, protocol, request, attachment_path, download_path=tempdir)

        except asyncio.IncompleteReadError:
            log.info('server', 'Connection closed prematurely')
        except Exception as e:
            log.warning('server', 'Exception in connection handler', exc=e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
