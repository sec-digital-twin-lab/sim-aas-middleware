import logging
import os
import socket
import time
from threading import Thread
from typing import Optional, List

import pytest
from pydantic import BaseModel

from simaas.core.helpers import hash_file_content
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.helpers import PortMaster
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.exceptions import PeerUnavailableError
from simaas.p2p.messenger import SecureMessenger, P2PMessage
from simaas.p2p.protocol import P2PProtocol, BroadcastResponse

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


class Bounce(BaseModel):
    value: str
    bounced_to: Optional[Identity]


class BounceRequest(BaseModel):
    value: int


class BounceResponse(BaseModel):
    value: int


class OneWayMessage(BaseModel):
    msg: str


class SimpleProtocol(P2PProtocol):
    def __init__(self, node):
        self._node = node
        super().__init__(node.identity, node.datastore, 'simple_protocol', [
            (BounceRequest, self._handle_bounce, BounceResponse),
            (OneWayMessage, self._handle_oneway, None)
        ])

    def send_bounce(self, peer_address: (str, int), value: int) -> int:
        response, attachment, peer = self.request(peer_address, BounceRequest(value=value))
        return response.value

    def send_oneway(self, peer_address: (str, int), msg: str) -> None:
        response, attachment, peer = self.request(peer_address, OneWayMessage(msg=msg))
        assert(response is None)
        assert(attachment is None)
        assert(peer is not None)

    def broadcast_bounce(self, network: List[NodeInfo], value: int) -> BroadcastResponse:
        return self.broadcast(network, BounceRequest(value=value), exclude_self=False)

    def _handle_bounce(self, request: BounceRequest, _: Identity) -> BounceResponse:
        return BounceResponse(value=request.value+1)

    def _handle_oneway(self, request: OneWayMessage, _: Identity) -> None:
        print(f"received one-way message: {request.msg}")


@pytest.fixture()
def p2p_node(test_context, keystore):
    node = test_context.get_node(keystore, use_dor=False, use_rti=False)
    return node


@pytest.fixture()
def server_identity(extra_keystores):
    return extra_keystores[0].identity


@pytest.fixture()
def client_identity(extra_keystores):
    return extra_keystores[1].identity


def test_simple_protocol(p2p_node):
    protocol = SimpleProtocol(p2p_node)
    p2p_node.p2p.add(protocol)

    value = protocol.send_bounce(p2p_node.info.p2p_address, 42)
    assert(value == 43)

    b_response = protocol.broadcast_bounce(p2p_node.db.get_network(), 42)
    assert(len(b_response.responses) == 1)
    assert(p2p_node.identity.id in b_response.responses)
    for response, attachment, peer in b_response.responses.values():
        assert(response.value == 43)
        assert(attachment is None)
        assert(peer.id == p2p_node.identity.id)

    protocol.send_oneway(p2p_node.info.p2p_address, 'hello!!!')


def test_unreachable(test_context, p2p_node):
    with pytest.raises(PeerUnavailableError):
        p2p_address = PortMaster.generate_p2p_address()
        SecureMessenger.connect(p2p_address, p2p_node.identity, test_context.testing_dir)


def test_secure_connect_accept(test_context, server_identity, client_identity):
    wd_path = test_context.testing_dir
    server_address = PortMaster.generate_p2p_address()

    class TestServer(Thread):
        def run(self):
            # create server socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(server_address)
            server_socket.listen(1)

            peer_socket, peer_address = server_socket.accept()

            # create messenger and perform handshake
            server_peer_identity, server_messenger = \
                SecureMessenger.accept(peer_socket, server_identity, wd_path)
            assert(server_peer_identity.id == client_identity.id)
            server_messenger.close()

            server_socket.close()

    server = TestServer()
    server.start()
    time.sleep(1)

    client_peer_identity, client_messenger = \
        SecureMessenger.connect(server_address, client_identity, wd_path)
    assert(client_peer_identity.id == server_identity.id)
    client_messenger.close()
    server.join()


def test_secure_send_receive_object(test_context, server_identity, client_identity):
    wd_path = test_context.testing_dir
    server_address = PortMaster.generate_p2p_address()

    class TestMessage(BaseModel):
        key1: str
        key2: int

    ref_obj = TestMessage(key1='value', key2=2)

    class TestServer(Thread):
        def run(self):
            # create server socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(server_address)
            server_socket.listen(1)

            peer_socket, peer_address = server_socket.accept()

            # create messenger and perform handshake
            server_peer_identity, server_messenger = \
                SecureMessenger.accept(peer_socket, server_identity, wd_path)
            # FIXME: Assertions do not work for threads in test
            assert(server_peer_identity.id == client_identity.id)

            server_obj = TestMessage.parse_obj(server_messenger._receive_object())
            assert(server_obj == ref_obj)
            server_messenger.close()

            server_socket.close()

    server = TestServer()
    server.start()
    time.sleep(1)

    client_peer_identity, client_messenger = SecureMessenger.connect(server_address, client_identity, wd_path)
    assert(client_peer_identity.id == server_identity.id)
    client_messenger._send_object(ref_obj.dict())
    client_messenger.close()
    server.join()


def test_secure_send_receive_stream(test_context, server_identity, client_identity):
    wd_path = test_context.testing_dir
    server_address = PortMaster.generate_p2p_address()

    # generate some data
    source_path = os.path.join(wd_path, 'source.dat')
    destination_path = os.path.join(wd_path, 'destination.dat')
    file_size = 5*1024*1024
    test_context.generate_random_file(source_path, file_size)
    file_hash = hash_file_content(source_path).hex()

    class TestServer(Thread):
        def run(self):
            # create server socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(server_address)
            server_socket.listen(1)

            peer_socket, peer_address = server_socket.accept()

            # create messenger and perform handshake
            server_peer_identity, server_messenger = \
                SecureMessenger.accept(peer_socket, server_identity, wd_path)
            assert(server_peer_identity.id == client_identity.id)

            server_file_size = server_messenger._receive_stream(destination_path)
            assert(server_file_size == file_size)

            server_file_hash = hash_file_content(destination_path).hex()
            assert(server_file_hash == file_hash)

            server_messenger.close()
            server_socket.close()

    server = TestServer()
    server.start()
    time.sleep(5)

    client_peer_identity, client_messenger = SecureMessenger.connect(server_address, client_identity, wd_path)
    assert(client_peer_identity.id == server_identity.id)
    client_messenger._send_stream(source_path)

    client_messenger.close()
    server.join()


def test_secure_send_receive_request(test_context, server_identity, client_identity):
    wd_path = test_context.testing_dir
    server_address = PortMaster.generate_p2p_address()

    class TestServer(Thread):
        def run(self):
            # create server socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(server_address)
            server_socket.listen(1)

            peer_socket, peer_address = server_socket.accept()

            # create messenger and perform handshake
            server_peer_identity, server_messenger = \
                SecureMessenger.accept(peer_socket, server_identity, wd_path)
            assert(server_peer_identity.id == client_identity.id)

            request: P2PMessage = server_messenger.receive_message()
            assert(request.type == 'Q')
            assert('question' in request.content)
            logger.debug(f"request received: {request}")

            server_messenger.send_response(P2PMessage.parse_obj({
                'protocol': request.protocol,
                'type': 'A',
                'content': {'answer': '42'},
                'attachment': None,
                'sequence_id': request.sequence_id
            }))
            server_messenger.close()

            server_socket.close()

    server = TestServer()
    server.start()
    time.sleep(1)

    client_peer_identity, client_messenger = SecureMessenger.connect(server_address, client_identity, wd_path)
    assert(client_peer_identity.id == server_identity.id)

    response = client_messenger.send_message(P2PMessage.parse_obj({
        'protocol': 'Hitchhiker',
        'type': 'Q',
        'content': {'question': 'What is the answer to the ultimate question of life, the universe and everything?'},
        'attachment': None,
        'sequence_id': None
    }))

    logger.debug(f"response received: {response}")
    assert(response.type == 'A')
    assert('answer' in response.content)
    assert(response.content['answer'] == '42')

    client_messenger.close()
    server.join()
