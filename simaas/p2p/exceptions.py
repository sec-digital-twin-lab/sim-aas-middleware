from simaas.core.exceptions import SaaSRuntimeException


class P2PException(SaaSRuntimeException):
    """
    Base exception class used for errors originating in the P2P subsystem.
    """


class PeerUnavailableError(P2PException):
    def __init__(self, details: dict = None) -> None:
        super().__init__('Peer is not available', details=details)


class UnexpectedP2PError(P2PException):
    def __init__(self, details: dict) -> None:
        super().__init__('Unexpected P2P error', details=details)

#
#
# class BootNodeUnavailableError(P2PException):
#     def __init__(self, details: dict) -> None:
#         super().__init__('Boot node is not available', details=details)
