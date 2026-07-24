import os
import socket
import socketserver
import ssl
import struct
import tempfile
import threading
from typing import Optional, Dict, Tuple

from simaas.core.errors import ConfigurationError
from simaas.core.helpers import env_int
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.p2p.base import (
    P2PProtocol, P2PMessage, build_server_ssl_context, identity_from_peercert,
    max_attachment_bytes, p2p_respond, p2p_send_error,
)

log = get_logger('simaas.p2p', 'p2p')

_CHUNK_SIZE = 1024 * 1024


class _ConnHandler(socketserver.BaseRequestHandler):
    """Handles a single peer connection. The TLS-wrapped socket is in self.request."""

    def handle(self) -> None:
        service: P2PService = self.server.simaas_service
        sock: ssl.SSLSocket = self.request
        try:
            peer_cert_der: Optional[bytes] = sock.getpeercert(binary_form=True)
            peer_identity = identity_from_peercert(service.node, peer_cert_der)

            raw_len = _recv_exact(sock, 4)
            (header_len,) = struct.unpack('>I', raw_len)
            header = _recv_exact(sock, header_len)
            request = P2PMessage.model_validate_json(header)

            protocol = service.lookup_protocol(request.protocol)
            if protocol is None:
                log.warning('server', 'Unsupported protocol', protocol=request.protocol)
                p2p_send_error(sock, request.protocol,
                               f"protocol '{request.protocol}' is not supported by this peer")
                return

            with tempfile.TemporaryDirectory() as tempdir:
                attachment_path: Optional[str] = None
                if request.attachment_size > 0:
                    cap = max_attachment_bytes()
                    if request.attachment_size > cap:
                        log.warning('server', 'Attachment too large',
                                    protocol=request.protocol,
                                    declared_size=request.attachment_size, cap=cap)
                        p2p_send_error(sock, request.protocol,
                                       f"attachment_size {request.attachment_size} "
                                       f"exceeds cap {cap}")
                        return
                    attachment_path = os.path.join(tempdir, 'attachment')
                    remaining = request.attachment_size
                    with open(attachment_path, 'wb') as f:
                        while remaining > 0:
                            chunk = sock.recv(min(_CHUNK_SIZE, remaining))
                            if not chunk:
                                log.warning('server', 'Attachment truncated', protocol=request.protocol)
                                return
                            f.write(chunk)
                            remaining -= len(chunk)

                p2p_respond(sock, protocol, request, peer_identity=peer_identity,
                            attachment_path=attachment_path, download_path=tempdir)

        except (IOError, ssl.SSLError, socket.error) as e:
            log.info('server', 'Connection error', exc=e)
        except Exception as e:
            log.warning('server', 'Exception in connection handler', exc=e)


def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    received = 0
    while received < n:
        got = sock.recv_into(view[received:])
        if got == 0:
            raise IOError(f'connection closed; expected {n} bytes, got {received}')
        received += got
    return bytes(buf)


_MAX_CONCURRENT_CONNECTIONS = env_int('SIMAAS_P2P_MAX_CONNS', 512, min_value=1, max_value=65535)
_CONN_SEM = threading.BoundedSemaphore(_MAX_CONCURRENT_CONNECTIONS)
_HANDSHAKE_TIMEOUT_SECONDS = env_int('SIMAAS_P2P_HANDSHAKE_TIMEOUT_SECONDS', 10, min_value=1, max_value=300)


class _ThreadedSSLServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int], handler_cls, ctx_builder):
        super().__init__(server_address, handler_cls, bind_and_activate=True)
        # Called per-accept so the trust bundle stays current with the identity DB.
        self._ctx_builder = ctx_builder
        self.simaas_service: Optional[P2PService] = None

    def get_request(self):
        raw_sock, addr = self.socket.accept()
        if not _CONN_SEM.acquire(blocking=False):
            log.warning('server', 'Connection cap reached; rejecting', addr=addr,
                        cap=_MAX_CONCURRENT_CONNECTIONS)
            try:
                raw_sock.close()
            except Exception:
                pass
            raise OSError('connection cap reached')
        try:
            raw_sock.settimeout(_HANDSHAKE_TIMEOUT_SECONDS)
            ssl_ctx = self._ctx_builder()
            tls_sock = ssl_ctx.wrap_socket(raw_sock, server_side=True)
            tls_sock.settimeout(None)
        except (ssl.SSLError, OSError) as e:
            log.warning('server', 'TLS handshake failed', addr=addr, exc=e)
            try:
                raw_sock.close()
            except Exception:
                pass
            _CONN_SEM.release()
            raise
        return tls_sock, addr

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            _CONN_SEM.release()

    def handle_error(self, request, client_address):
        # Suppress noisy tracebacks for failed TLS handshakes and dropped clients.
        log.info('server', 'Request handling error', client=client_address)


class P2PService:
    def __init__(self, keystore: Keystore, address: str) -> None:
        self._keystore = keystore
        self._address = address
        self._port = int(address.split(':')[-1])
        self._protocols: Dict[str, P2PProtocol] = {}
        self._server: Optional[_ThreadedSSLServer] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._node = None

    def set_node(self, node) -> None:
        """Back-reference for trust bundle building and identity lookups (reads node.db)."""
        self._node = node

    @property
    def node(self):
        return self._node

    def is_ready(self) -> bool:
        return self._server is not None

    def add(self, protocol: P2PProtocol) -> None:
        cls = type(protocol)
        public = getattr(cls, "_p2p_public_access", False)
        auth_required = getattr(cls, "_p2p_requires_authentication", False)
        if public and auth_required:
            raise RuntimeError(
                f"P2P protocol {cls.__name__} carries both @p2p_public_access "
                f"and @p2p_requires_authentication; pick one"
            )
        if not public and not auth_required:
            raise RuntimeError(
                f"P2P protocol {cls.__name__} has no auth marker; add "
                f"@p2p_public_access or @p2p_requires_authentication from "
                f"simaas.decorators"
            )
        log.info('protocol', 'Adding P2P protocol', name=protocol.name())
        self._protocols[protocol.name()] = protocol

    def lookup_protocol(self, name: str) -> Optional[P2PProtocol]:
        return self._protocols.get(name)

    def address(self) -> str:
        return self._address

    def port(self) -> int:
        return self._port

    def fq_address(self) -> str:
        fqdn = socket.getfqdn()
        return f"tcp://{fqdn}:{self._port}"

    def _current_trust_bundle(self) -> Optional[str]:
        """Concatenate every known peer's TLS cert into a PEM bundle for mTLS verification."""
        if self._node is None:
            return None
        parts = [i.tls_cert for i in self._node.db.get_identities() if i.tls_cert]
        return "\n".join(parts) if parts else None

    def _build_server_ctx(self) -> ssl.SSLContext:
        return build_server_ssl_context(
            self._keystore.tls_cert_pem(),
            self._keystore.tls_key_pem(),
            trusted_peer_certs_pem=self._current_trust_bundle(),
        )

    def start_service_background(self) -> None:
        self._ready_event.clear()
        host, port = self._parse_addr()
        try:
            self._server = _ThreadedSSLServer((host, port), _ConnHandler, self._build_server_ctx)
            self._server.simaas_service = self
        except Exception as e:
            raise ConfigurationError(
                path='p2p.socket', expected='socket binding', actual=str(e),
                hint='P2P server cannot be created',
            )
        log.info('server', 'P2P server listening', address=self._address)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._ready_event.set()

    def _parse_addr(self) -> Tuple[str, int]:
        addr = self._address
        if addr.startswith('tcp://'):
            addr = addr[len('tcp://'):]
        host, port = addr.rsplit(':', 1)
        return host, int(port)

    def stop_service(self) -> None:
        if self._server is None:
            return
        log.info('server', 'Initiating P2P service shutdown')
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as e:
            log.warning('server', 'Error stopping P2P server', exc=e)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._server = None
        log.info('server', 'P2P service shutdown complete')

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        return self._ready_event.wait(timeout=timeout)
