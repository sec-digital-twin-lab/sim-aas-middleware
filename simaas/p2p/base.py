import abc
import json
import os
import socket
import ssl
import struct
import tempfile
import traceback
from typing import Optional, Tuple, TYPE_CHECKING

import canonicaljson
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from pydantic import BaseModel

from simaas.core.errors import NetworkError
from simaas.core.helpers import env_int
from simaas.core.logging import get_logger

if TYPE_CHECKING:
    from simaas.core.identity import Identity
    from simaas.core.keystore import Keystore

log = get_logger('simaas.p2p', 'p2p')


class P2PMessage(BaseModel):
    protocol: str
    type: str
    content: Optional[dict]
    attachment_size: int


class P2PAddress(BaseModel):
    address: str
    peer_tls_cert: str


class RelayAttestation(BaseModel):
    """Signed assertion identifying the original sender for a relayed action.

    Lets the target verify the original authorisation directly, separately from
    the immediate (relay) sender's transport-level identity.
    """
    iid: str
    signature: str
    issued_at: int


_RELAY_ATTESTATION_DOMAIN = b'p2p-relay-attestation:v1:'


def _relay_attestation_token(payload: dict) -> bytes:
    """Domain-separated canonical token for a relay attestation signature."""
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(_RELAY_ATTESTATION_DOMAIN)
    digest.update(canonicaljson.encode_canonical_json(payload))
    return digest.finalize()


def sign_relay_attestation(keystore: "Keystore", payload: dict) -> RelayAttestation:
    """Build a ``RelayAttestation`` for the given attested-fields payload."""
    return RelayAttestation(
        iid=keystore.identity.id,
        signature=keystore.sign(_relay_attestation_token(payload)),
        issued_at=payload['issued_at'],
    )


def verify_relay_attestation(identity: "Identity", payload: dict, signature: str) -> bool:
    """Verify a relay attestation against the claimed identity's public key."""
    return identity.verify(_relay_attestation_token(payload), signature)


class P2PProtocol(abc.ABC):
    def __init__(self, protocol: str) -> None:
        self._protocol = protocol

    def name(self) -> str:
        return self._protocol

    @abc.abstractmethod
    def handle(
            self, request: BaseModel, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional["Identity"] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        """Process a P2P request.

        ``identity`` is the verified sender — set when the caller presented a TLS
        client cert that resolves to a known identity. Public-access protocols
        may receive ``None`` (anonymous caller). Auth-required protocols are
        rejected by ``p2p_respond`` before ``handle`` is invoked if ``identity``
        would be ``None``.
        """

    @staticmethod
    def request_type() -> BaseModel:
        ...

    @staticmethod
    def response_type() -> Optional[BaseModel]:
        ...


# Minimum assumed throughput for size-aware timeout (bytes per second).
_THROUGHPUT_FLOOR = 10 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024

# Inbound P2P attachment ceiling; tune via ``SIMAAS_P2P_MAX_ATTACHMENT_BYTES``.
_DEFAULT_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024 * 1024
_MIN_ATTACHMENT_BYTES = 1024 * 1024
_MAX_ATTACHMENT_BYTES = 1024 * 1024 * 1024 * 1024


def max_attachment_bytes() -> int:
    return env_int(
        'SIMAAS_P2P_MAX_ATTACHMENT_BYTES', _DEFAULT_MAX_ATTACHMENT_BYTES,
        min_value=_MIN_ATTACHMENT_BYTES, max_value=_MAX_ATTACHMENT_BYTES,
    )


def _parse_tcp_address(address: str) -> Tuple[str, int]:
    if address.startswith('tcp://'):
        address = address[len('tcp://'):]
    host, port = address.rsplit(':', 1)
    return host, int(port)


def _write_pem_pair(cert_pem: bytes, key_pem: bytes) -> Tuple[str, str]:
    cf = tempfile.NamedTemporaryFile(mode='wb', suffix='.pem', delete=False)
    kf = tempfile.NamedTemporaryFile(mode='wb', suffix='.pem', delete=False)
    cf.write(cert_pem)
    cf.close()
    kf.write(key_pem)
    kf.close()
    return cf.name, kf.name


def _unlink_silently(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def build_client_ssl_context(peer_cert_pem: str, client_keystore: Optional["Keystore"] = None) -> ssl.SSLContext:
    """Client context pinning the peer's self-signed cert as the trusted CA.

    When ``client_keystore`` is supplied the client also presents its own
    cert/key pair so the server can identify the caller via mTLS. Pass ``None``
    to call public-access protocols anonymously.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=peer_cert_pem)
    if client_keystore is not None:
        cert_path, key_path = _write_pem_pair(client_keystore.tls_cert_pem(), client_keystore.tls_key_pem())
        try:
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        finally:
            _unlink_silently(cert_path, key_path)
    return ctx


def build_server_ssl_context(cert_pem: bytes, key_pem: bytes,
                             trusted_peer_certs_pem: Optional[str] = None) -> ssl.SSLContext:
    """Server context presenting its own self-signed cert and accepting trusted client certs.

    When ``trusted_peer_certs_pem`` is provided (a concatenation of known peer
    identity certs in PEM form), the server requests a client cert in the
    handshake and accepts it only if it appears in that bundle. This is the
    transport-layer half of the P2P auth model. When the bundle is empty
    (e.g. on a freshly-started node before any peers are known), the server
    falls back to no client-cert request so the bootstrap flow still works.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    cert_path, key_path = _write_pem_pair(cert_pem, key_pem)
    try:
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    finally:
        _unlink_silently(cert_path, key_path)
    if trusted_peer_certs_pem:
        ctx.load_verify_locations(cadata=trusted_peer_certs_pem)
        ctx.verify_mode = ssl.CERT_OPTIONAL
    else:
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def identity_from_peercert(node, peer_cert_der: Optional[bytes]) -> Optional["Identity"]:
    """Resolve a verified TLS peer cert to a known node identity.

    Returns ``None`` if the handshake didn't yield a peer cert (anonymous
    caller) or if the cert doesn't match any identity the node knows about.
    PEM-equality keeps this independent of any pre-computed cert index.
    """
    if not peer_cert_der or node is None:
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
        cert = x509.load_der_x509_certificate(peer_cert_der)
        peer_pem = cert.public_bytes(Encoding.PEM).decode('utf-8').strip()
    except Exception:
        return None
    for known in node.db.get_identities():
        if known.tls_cert and known.tls_cert.strip() == peer_pem:
            return known
    return None


def _send_frame(sock: socket.socket, message: P2PMessage, attachment_path: Optional[str] = None) -> None:
    header = json.dumps(message.model_dump()).encode('utf-8')
    sock.sendall(struct.pack('>I', len(header)) + header)
    if attachment_path:
        with open(attachment_path, 'rb') as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendall(chunk)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    received = 0
    while received < n:
        got = sock.recv_into(view[received:])
        if got == 0:
            raise IOError(f'connection closed; expected {n} bytes, got {received}')
        received += got
    return bytes(buf)


def _recv_header(sock: socket.socket) -> P2PMessage:
    raw_len = _recv_exact(sock, 4)
    (header_len,) = struct.unpack('>I', raw_len)
    header = _recv_exact(sock, header_len)
    return P2PMessage.model_validate_json(header)


def _recv_attachment(sock: socket.socket, size: int, download_path: Optional[str]) -> Optional[str]:
    if size <= 0:
        return None
    cap = max_attachment_bytes()
    if size > cap:
        raise IOError(f'attachment_size {size} exceeds cap {cap}')
    if download_path is None:
        download_path = os.devnull
    remaining = size
    with open(download_path, 'wb') as f:
        while remaining > 0:
            chunk = sock.recv(min(_CHUNK_SIZE, remaining))
            if not chunk:
                raise IOError('attachment truncated')
            f.write(chunk)
            remaining -= len(chunk)
    return download_path


def p2p_request(
        peer: P2PAddress, protocol: str, content: BaseModel, reply_type: Optional[BaseModel] = None,
        attachment_path: Optional[str] = None, download_path: Optional[str] = None,
        timeout: Optional[int] = None, with_authorisation_by: Optional["Keystore"] = None,
) -> Tuple[Optional[BaseModel], Optional[str]]:
    attachment_size = os.path.getsize(attachment_path) if attachment_path else 0
    base_timeout_ms = timeout if timeout is not None else 5000
    effective_timeout_s = max(base_timeout_ms, int(attachment_size / _THROUGHPUT_FLOOR * 1000)) / 1000.0

    host, port = _parse_tcp_address(peer.address)
    ssl_ctx = build_client_ssl_context(peer.peer_tls_cert, client_keystore=with_authorisation_by)

    raw_sock: Optional[socket.socket] = None
    sock: Optional[ssl.SSLSocket] = None
    try:
        try:
            raw_sock = socket.create_connection((host, port), timeout=effective_timeout_s)
            sock = ssl_ctx.wrap_socket(raw_sock, server_hostname='simaas-node')
            sock.settimeout(effective_timeout_s)
        except (socket.timeout, OSError, ssl.SSLError) as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise NetworkError(peer_address=peer.address, operation='connect',
                               timeout_ms=int(effective_timeout_s * 1000), trace=trace)

        request = P2PMessage(
            protocol=protocol, type='request', content=content.model_dump(),
            attachment_size=attachment_size,
        )
        try:
            _send_frame(sock, request, attachment_path)
        except (socket.timeout, OSError, ssl.SSLError) as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise NetworkError(peer_address=peer.address, operation='send',
                               timeout_ms=int(effective_timeout_s * 1000), trace=trace)

        try:
            reply = _recv_header(sock)
            if reply.type == 'error':
                err_content = reply.content or {}
                raise NetworkError(
                    peer_address=peer.address, operation=protocol,
                    reason=err_content.get('reason', 'peer reported an error'),
                    **{k: v for k, v in err_content.items() if k != 'reason'},
                )
            reply_attachment = _recv_attachment(sock, reply.attachment_size, download_path)
        except (socket.timeout, OSError, ssl.SSLError, IOError) as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise NetworkError(peer_address=peer.address, operation='receive',
                               timeout_ms=int(effective_timeout_s * 1000), trace=trace)

        if reply_type is not None and reply.content is not None:
            return reply_type.model_validate(reply.content), reply_attachment
        return None, reply_attachment

    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        elif raw_sock is not None:
            try:
                raw_sock.close()
            except Exception:
                pass


def p2p_send_error(sock: socket.socket, protocol_name: str, reason: str, **extra) -> None:
    try:
        reply = P2PMessage(
            protocol=protocol_name, type='error',
            content={'reason': reason, **extra},
            attachment_size=0,
        )
        _send_frame(sock, reply)
    except Exception as e:
        log.warning('respond', 'Failed to send P2P error reply', exc=e, protocol=protocol_name)


def p2p_respond(sock: socket.socket, protocol: P2PProtocol, request: P2PMessage,
                peer_identity: Optional["Identity"] = None,
                attachment_path: Optional[str] = None, download_path: Optional[str] = None) -> None:
    """Dispatch a P2P request to its handler with marker-driven auth enforcement.

    ``peer_identity`` is the verified identity behind the TLS handshake (or
    ``None`` when the caller didn't present a cert). The protocol's class
    marker decides whether ``None`` is acceptable.
    """
    try:
        cls = type(protocol)
        auth_required = getattr(cls, "_p2p_requires_authentication", False)

        if auth_required and peer_identity is None:
            p2p_send_error(sock, protocol.name(),
                           "authentication required", auth='missing')
            return

        request_type = protocol.request_type()
        reply_content, reply_attachment_path = protocol.handle(
            request_type.model_validate(request.content), attachment_path, download_path,
            identity=peer_identity,
        )
        reply_attachment_size = os.path.getsize(reply_attachment_path) if reply_attachment_path else 0
        try:
            reply = P2PMessage(
                protocol=protocol.name(), type='reply',
                content=reply_content.model_dump() if reply_content else None,
                attachment_size=reply_attachment_size,
            )
            _send_frame(sock, reply, reply_attachment_path)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            log.error('respond', 'Unexpected P2P error', exc=e)
            raise NetworkError(operation='respond', trace=trace)
    finally:
        if attachment_path and os.path.isfile(attachment_path):
            os.remove(attachment_path)
