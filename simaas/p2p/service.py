import time
import threading
import socket
import traceback

from threading import Lock
from typing import Optional

from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.p2p.exceptions import P2PException, UnsupportedProtocolError, UnexpectedMessageTypeError
from simaas.p2p.protocol import SecureMessenger, P2PProtocol

logger = Logging.get('p2p.service')


class P2PService:
    def __init__(self, node, address: (str, int), bind_all_address: bool) -> None:
        self._mutex = Lock()
        self._node = node
        self._address = address
        self._p2p_service_socket: Optional[socket.socket] = None
        self._is_server_running = False
        self._is_server_stopped = True
        self._registered_protocols: dict[str, P2PProtocol] = {}
        self._bind_all_address = bind_all_address

    def add(self, protocol: P2PProtocol) -> None:
        """
        Adds a protocol to the P2P service.
        :param protocol: an instance of the protocol class (must inherit from P2PProtocol class)
        :return:
        """
        with self._mutex:
            logger.info(f"add support for p2p protocol '{protocol.name}'")
            self._registered_protocols[protocol.name] = protocol

    def address(self) -> (str, int):
        """
        Returns the address (host:port) of the P2P service.
        :return: address (host:port).
        """
        return self._address

    def start_service(self, concurrency: int = 5) -> None:
        """
        Starts the TCP socket server at the specified address, allowing for some degree of concurrency. A separate
        thread is started for handling incoming connections.
        :param concurrency: the degree of concurrency (default: 5)
        :return: None
        """
        with self._mutex:
            if not self._p2p_service_socket:
                try:
                    # create server socket
                    self._p2p_service_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self._p2p_service_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self._p2p_service_socket.bind(self._address if not self._bind_all_address
                                                  else ("0.0.0.0", self._address[1]))
                    self._p2p_service_socket.listen(concurrency)
                    logger.info(f"[{self._node.identity.name}] p2p server initialised at address '{self._address}'")
                except Exception as e:
                    raise SaaSRuntimeException("P2P server socket cannot be created", details={
                        'exception': e
                    })

                # start the server thread
                thread = threading.Thread(target=self._handle_incoming_connections)
                thread.daemon = True
                thread.start()

    def stop_service(self) -> None:
        """
        Instructs the server thread to stop accepting incoming connections and to shutdown the server socket. This
        may take a few seconds. Note: this method is blocking (i.e., it will return once the server has stopped).
        :return:
        """
        with self._mutex:
            if self._p2p_service_socket:
                logger.info(f"[{self._node.identity.name}] initiate shutdown of p2p service")
                self._is_server_running = False
                while not self._is_server_stopped:
                    time.sleep(0.5)

    def _handle_incoming_connections(self):
        logger.info(f"[{self._node.identity.name}] start listening to incoming p2p connections...")

        # main loop for listening for incoming connections
        self._is_server_running = True
        self._is_server_stopped = False
        self._p2p_service_socket.settimeout(2.0)

        while self._is_server_running:
            try:
                # accept incoming connection
                peer_socket, _ = self._p2p_service_socket.accept()

                # create messenger and perform handshake
                peer, messenger = SecureMessenger.accept(peer_socket, self._node.identity, self._node.datastore)

                # start handling the client requests
                threading.Thread(target=self._handle_client, args=(peer, messenger)).start()

            except socket.timeout:
                pass

            except P2PException as e:
                logger.warning(f"[{self._node.identity.name}] error while accepting incoming connection: {e}")

            except Exception as e:
                logger.warning(f"[{self._node.identity.name}] unhandled exception type in server loop: {e}")

        logger.info(f"[{self._node.identity.name}] stop listening to incoming p2p connections")

        self._p2p_service_socket.close()
        self._p2p_service_socket = None
        self._is_server_stopped = True

    def _handle_client(self, peer: Identity, messenger: SecureMessenger):
        logger.debug(f"[{self._node.identity.name}] begin serving client '{peer.id}'")

        try:
            request = messenger.receive_message()

            # is the protocol supported?
            if request.protocol not in self._registered_protocols:
                raise UnsupportedProtocolError({
                    'request': request.dict(),
                    'supported': [*self._registered_protocols.keys()]
                })

            # is the message type supported by the protocol?
            protocol: P2PProtocol = self._registered_protocols[request.protocol]
            if not protocol.supports(request.type):
                raise UnexpectedMessageTypeError({
                    'request': request.dict(),
                    'type': request.type,
                    'protocol_name': protocol.name
                })

            # let the protocol handle the message and send response back to peer (if any)
            response = protocol.handle_message(request, peer)
            if response:
                response.sequence_id = request.sequence_id
                messenger.send_response(response)
            else:
                messenger.send_null()

        except P2PException as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[{self._node.identity.name}] problem encountered while handling "
                           f"client '{peer.id}: {e}\n{trace}")

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[{self._node.identity.name}] unhandled exception while serving "
                           f"client '{peer.id}': {e}\n{trace}")

        messenger.close()
        logger.debug(f"[{self._node.identity.name}] done serving client '{peer.id}'")
