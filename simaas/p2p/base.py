import abc
import asyncio
import json
import os
import ssl
import struct
import tempfile
import traceback
from typing import Optional, Tuple

from pydantic import BaseModel

from simaas.core.errors import NetworkError
from simaas.core.logging import get_logger

log = get_logger('simaas.p2p', 'p2p')


class P2PMessage(BaseModel):
    protocol: str
    type: str
    content: Optional[dict]
    attachment_size: int


class P2PAddress(BaseModel):
    address: str
    peer_tls_cert: str


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


# Minimum assumed throughput for size-aware timeout (bytes per second).
_THROUGHPUT_FLOOR = 10 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


def _parse_tcp_address(address: str) -> Tuple[str, int]:
    if address.startswith('tcp://'):
        address = address[len('tcp://'):]
    host, port = address.rsplit(':', 1)
    return host, int(port)


def _write_pem_pair(cert_pem: bytes, key_pem: bytes) -> Tuple[str, str]:
    cf = tempfile.NamedTemporaryFile(mode='wb', suffix='.pem', delete=False)
    kf = tempfile.NamedTemporaryFile(mode='wb', suffix='.pem', delete=False)
    cf.write(cert_pem); cf.close()
    kf.write(key_pem); kf.close()
    return cf.name, kf.name


def _unlink_silently(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def build_client_ssl_context(peer_cert_pem: str) -> ssl.SSLContext:
    """Client context pinning the peer's self-signed cert as the only trusted CA."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=peer_cert_pem)
    return ctx


def build_server_ssl_context(cert_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
    """Server context presenting its own self-signed cert. Clients are not authenticated at TLS level."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    cert_path, key_path = _write_pem_pair(cert_pem, key_pem)
    try:
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    finally:
        _unlink_silently(cert_path, key_path)
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _send_frame(writer: asyncio.StreamWriter, message: P2PMessage,
                      attachment_path: Optional[str] = None) -> None:
    header = json.dumps(message.model_dump()).encode('utf-8')
    writer.write(struct.pack('>I', len(header)))
    writer.write(header)
    await writer.drain()
    if attachment_path:
        with open(attachment_path, 'rb') as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()


async def _recv_header(reader: asyncio.StreamReader) -> P2PMessage:
    raw_len = await reader.readexactly(4)
    (header_len,) = struct.unpack('>I', raw_len)
    header = await reader.readexactly(header_len)
    return P2PMessage.model_validate_json(header)


async def _recv_attachment(reader: asyncio.StreamReader, size: int, download_path: Optional[str]) -> Optional[str]:
    if size <= 0:
        return None
    if download_path is None:
        download_path = os.devnull
    remaining = size
    with open(download_path, 'wb') as f:
        while remaining > 0:
            chunk = await reader.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                raise IOError('attachment truncated')
            f.write(chunk)
            remaining -= len(chunk)
    return download_path


async def p2p_request(
        peer: P2PAddress, protocol: str, content: BaseModel, reply_type: Optional[BaseModel] = None,
        attachment_path: Optional[str] = None, download_path: Optional[str] = None,
        timeout: Optional[int] = None,
) -> Tuple[Optional[BaseModel], Optional[str]]:
    attachment_size = os.path.getsize(attachment_path) if attachment_path else 0
    base_timeout_ms = timeout if timeout is not None else 5000
    effective_timeout_s = max(base_timeout_ms, int(attachment_size / _THROUGHPUT_FLOOR * 1000)) / 1000.0

    host, port = _parse_tcp_address(peer.address)
    ssl_ctx = build_client_ssl_context(peer.peer_tls_cert)

    writer: Optional[asyncio.StreamWriter] = None
    try:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ssl_ctx, server_hostname='simaas-node'),
                timeout=effective_timeout_s,
            )
        except (asyncio.TimeoutError, OSError, ssl.SSLError) as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise NetworkError(peer_address=peer.address, operation='connect',
                               timeout_ms=int(effective_timeout_s * 1000), trace=trace)

        request = P2PMessage(
            protocol=protocol, type='request', content=content.model_dump(),
            attachment_size=attachment_size,
        )
        try:
            await asyncio.wait_for(_send_frame(writer, request, attachment_path), timeout=effective_timeout_s)
        except (asyncio.TimeoutError, OSError, ssl.SSLError) as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise NetworkError(peer_address=peer.address, operation='send',
                               timeout_ms=int(effective_timeout_s * 1000), trace=trace)

        try:
            reply = await asyncio.wait_for(_recv_header(reader), timeout=effective_timeout_s)
            if reply.type == 'error':
                err_content = reply.content or {}
                raise NetworkError(
                    peer_address=peer.address, operation=protocol,
                    reason=err_content.get('reason', 'peer reported an error'),
                    **{k: v for k, v in err_content.items() if k != 'reason'},
                )
            reply_attachment = await asyncio.wait_for(
                _recv_attachment(reader, reply.attachment_size, download_path),
                timeout=effective_timeout_s,
            )
        except (asyncio.TimeoutError, OSError, ssl.SSLError, IOError) as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise NetworkError(peer_address=peer.address, operation='receive',
                               timeout_ms=int(effective_timeout_s * 1000), trace=trace)

        if reply_type is not None and reply.content is not None:
            return reply_type.model_validate(reply.content), reply_attachment
        return None, reply_attachment

    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def p2p_send_error(writer: asyncio.StreamWriter, protocol_name: str, reason: str, **extra) -> None:
    try:
        reply = P2PMessage(
            protocol=protocol_name, type='error',
            content={'reason': reason, **extra},
            attachment_size=0,
        )
        await _send_frame(writer, reply)
    except Exception as e:
        log.warning('respond', 'Failed to send P2P error reply', exc=e, protocol=protocol_name)


async def p2p_respond(writer: asyncio.StreamWriter, protocol: P2PProtocol, request: P2PMessage,
                      attachment_path: Optional[str] = None, download_path: Optional[str] = None) -> None:
    try:
        request_type = protocol.request_type()
        reply_content, reply_attachment_path = await protocol.handle(
            request_type.model_validate(request.content), attachment_path, download_path,
        )
        reply_attachment_size = os.path.getsize(reply_attachment_path) if reply_attachment_path else 0
        try:
            reply = P2PMessage(
                protocol=protocol.name(), type='reply',
                content=reply_content.model_dump() if reply_content else None,
                attachment_size=reply_attachment_size,
            )
            await _send_frame(writer, reply, reply_attachment_path)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            log.error('respond', 'Unexpected P2P error', exc=e)
            raise NetworkError(operation='respond', trace=trace)
    finally:
        if attachment_path and os.path.isfile(attachment_path):
            os.remove(attachment_path)
