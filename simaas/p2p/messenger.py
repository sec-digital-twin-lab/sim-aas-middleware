from __future__ import annotations

import math
import os
import json
import base64
import socket
from typing import Optional

from pydantic import BaseModel

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet

from simaas.core.eckeypair import ECKeyPair
from simaas.core.helpers import generate_random_string
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.p2p.exceptions import ReceiveDataError, SendDataError, MismatchingBytesWrittenError, ResourceNotFoundError, \
    HandshakeFailedError, PeerUnavailableError, P2PException

logger = Logging.get('p2p.messenger')


class P2PMessage(BaseModel):
    protocol: str
    type: str
    content: Optional[dict]
    attachment: Optional[str]
    sequence_id: Optional[int]


class SecureStreamPreamble(BaseModel):
    content_size: int
    n_chunks: int


class SecureMessenger:
    default_chunk_size = 1024*1024

    """
    SecureMessenger is a wrapper for a TCP socket connection. It uses encryption to secure the communication between
    two nodes. For this purpose, a key exchange handshake is performed immediately upon establishing a connection.
    All message exchange henceforth is encrypted.
    """

    def __init__(self, peer_socket: socket, storage_path: str):
        self._peer_socket = peer_socket
        self._storage_path = storage_path
        self._cipher: Optional[Fernet] = None

    @classmethod
    def connect(cls, peer_address: (str, int), identity: Identity, storage_path: str,
                timeout: int = 2) -> (Identity, SecureMessenger):
        """
        Attempts to connect to a peer by performing a handshake once the connection is established.
        :param peer_address: the address (host:port) of the peer
        :param identity: the identity of the peer's counterparty
        :param storage_path: path to where attachments are being stored
        :param timeout:
        :return: the identity of the peer and the SecureMessenger object if successful.
        :raise PeerUnavailableError
        :raise HandshakeFailedError
        """
        try:
            # try to establish a socket connection to the peer and create a messenger
            messenger = SecureMessenger(socket.create_connection(peer_address, timeout=timeout), storage_path)
            peer = messenger._handshake(identity)
            return peer, messenger

        except ConnectionRefusedError:
            raise PeerUnavailableError({
                'peer_address': peer_address
            })

    @classmethod
    def accept(cls, peer_socket: socket.socket, identity: Identity, storage_path: str) -> (Identity, SecureMessenger):
        """
        Attempts to accept an incoming connection from a peer by performing a handshake.
        :param peer_socket: the socket for the peer
        :param identity: the identity of the peer's counterparty
        :param storage_path: path to where attachments are being stored
        :return: the identity of the peer and the SecureMessenger object if successful.
        """
        messenger = SecureMessenger(peer_socket, storage_path)
        peer = messenger._handshake(identity)
        return peer, messenger

    def close(self) -> None:
        """
        Closes the connection.
        :return: None
        """
        if self._peer_socket:
            self._peer_socket.close()

    def send_message(self, request: P2PMessage) -> Optional[P2PMessage]:
        """
        Sends a request and waits for a response.
        :param request: the request message
        :return: the response
        """

        # check if the attachment exists (if applicable)
        if request.attachment and not os.path.isfile(request.attachment):
            raise FileNotFoundError(f"attachment at {request.attachment} does not exist")

        # send the request content, followed by the attachment (if any)
        self._send_object(request.dict())
        if request.attachment:
            self._send_stream(request.attachment)

        response = self.receive_message()
        return response

    def receive_message(self) -> Optional[P2PMessage]:
        """
        Receives a request, i.e., a dict with the following keys: 'type', 'request_id', 'content', 'has_attachment'.
        :return: the request
        """
        received = self._receive_object()
        if not received:
            return None

        # convert the object into a P2P message
        request = P2PMessage.parse_obj(received)

        # receive the attachment (if any)
        if request.attachment:
            request.attachment = os.path.join(self._storage_path, f"attachment_{generate_random_string(16)}")
            self._receive_stream(request.attachment)

        return request

    def send_response(self, response: P2PMessage) -> None:
        """
        Sends a response to a previously received request.
        :param response: the response message
        :return:
        """

        # check if the attachment exists (if applicable)
        if response.attachment and not os.path.isfile(response.attachment):
            raise ResourceNotFoundError({
                'attachment_path': response.attachment
            })

        # send the response content, followed by the attachment (if any)
        self._send_object(response.dict())
        if response.attachment:
            self._send_stream(response.attachment)

    def send_null(self) -> None:
        length = 0
        self._send_data(length.to_bytes(4, byteorder='big'))

    def _handshake(self, identity: Identity) -> Identity:
        try:
            # generate an ephemeral key pair
            key = ECKeyPair.create_new()

            # send and receive peer public key information
            self._send_chunk(key.public_as_bytes())
            peer_key = ECKeyPair.from_public_key_bytes(self._receive_chunk())

            # generate the shared key
            shared_key = key.private_key.exchange(ec.ECDH(), peer_key.public_key)
            session_key = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=None,
                backend=default_backend()
            ).derive(shared_key)

            # initialise the cipher used to encrypt/decrypt messages
            self._cipher = Fernet(base64.urlsafe_b64encode(session_key))

            # exchange identities. note that this is not strictly speaking part of the handshake. it is merely for the
            # benefit of the peers to know who their counterparty is.
            self._send_object(identity.dict())
            self.peer = Identity.parse_obj(self._receive_object())

            return self.peer

        except P2PException as e:
            raise HandshakeFailedError({
                'e': e
            })

        except Exception as e:
            logger.error(f"unhandled non-SaaS exception: {e}")
            raise HandshakeFailedError({
                'e': e
            })

    def _send_object(self, obj: dict) -> int:
        # deflate and encode the object
        obj = json.dumps(obj)
        obj = obj.encode('utf-8')
        encrypted_obj = self._cipher.encrypt(obj)

        # send the message
        length = len(encrypted_obj)
        total_sent = self._send_data(length.to_bytes(4, byteorder='big'))
        total_sent += self._send_data(encrypted_obj)
        return total_sent

    def _receive_object(self) -> Optional[dict]:
        # receive the message length
        length = int.from_bytes(self._receive_data(4), 'big')
        if length > 0:
            # receive the encrypted object
            encrypted_obj = self._receive_data(length)

            # decrypt and inflate the object
            obj = self._cipher.decrypt(encrypted_obj)
            obj = obj.decode('utf-8')
            obj = json.loads(obj)

        else:
            obj = None

        return obj

    def _send_stream(self, source: str, chunk_size: int = None) -> int:
        # does the file exist?
        if not os.path.isfile(source):
            raise ResourceNotFoundError({
                'source': source
            })

        # determine the chunk size
        chunk_size = chunk_size if chunk_size else SecureMessenger.default_chunk_size

        # send the preamble
        content_size = os.path.getsize(source)
        n_chunks = int(math.ceil(content_size / chunk_size))
        preamble = SecureStreamPreamble(content_size=content_size, n_chunks=n_chunks)
        total_sent = self._send_object(preamble.dict())

        # read from the source and send the stream of chunks
        with open(source, 'rb') as f:
            # read a chunk and encrypt it
            chunk = f.read(chunk_size)
            while chunk:
                total_sent += self._send_chunk(chunk)
                chunk = f.read(chunk_size)

        return total_sent

    def _receive_stream(self, destination: str) -> int:
        # receive the preamble
        preamble = self._receive_object()
        preamble = SecureStreamPreamble.parse_obj(preamble)

        # read all the chunks and write to the file
        total_written = 0
        with open(destination, 'wb') as f:
            for i in range(preamble.n_chunks):
                chunk = self._receive_chunk()
                f.write(chunk)
                total_written += len(chunk)

        # verify if the bytes written is the correct file size
        if total_written != preamble.content_size:
            raise MismatchingBytesWrittenError({
                'preamble': preamble,
                'total_written': total_written
            })

        return total_written

    def _send_chunk(self, chunk: bytes) -> int:
        chunk = self._cipher.encrypt(chunk) if self._cipher else chunk
        chunk_length = len(chunk)
        total_sent = self._send_data(chunk_length.to_bytes(4, byteorder='big'))
        total_sent += self._send_data(chunk)
        return total_sent

    def _receive_chunk(self) -> bytes:
        chunk_length = int.from_bytes(self._receive_data(4), 'big')
        chunk = self._receive_data(chunk_length)
        chunk = self._cipher.decrypt(chunk) if self._cipher else chunk
        return chunk

    def _send_data(self, data: bytes) -> int:
        total_sent = 0
        while total_sent < len(data):
            sent = self._peer_socket.send(data[total_sent:])
            if sent == 0:
                raise SendDataError({
                    'sent': sent,
                    'peer_socket': self._peer_socket,
                    'data': data,
                    'total_sent': total_sent,
                })
            total_sent += sent
        return total_sent

    def _receive_data(self, length: int) -> bytes:
        chunks = []
        received = 0
        while received < length:
            chunk = self._peer_socket.recv(min(length - received, SecureMessenger.default_chunk_size))
            if chunk == b'':
                raise ReceiveDataError({
                    'chunk': chunk,
                    'peer_socket': self._peer_socket,
                    'received': received,
                    'length': length,
                    'default_chunk_size': SecureMessenger.default_chunk_size
                })

            chunks.append(chunk)
            received += len(chunk)

        return b''.join(chunks)
