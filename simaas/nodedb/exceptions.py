from simaas.core.exceptions import SaaSRuntimeException


class NodeDBException(SaaSRuntimeException):
    """
    Base exception class used for errors originating in the NodeDB subsystem.
    """


class InvalidIdentityError(NodeDBException):
    def __init__(self, details: dict) -> None:
        super().__init__('Identity is not valid', details=details)


class UnexpectedIdentityError(NodeDBException):
    def __init__(self, details: dict) -> None:
        super().__init__('Unexpected identity encountered', details=details)


class IdentityNotFoundError(NodeDBException):
    def __init__(self, iid: str) -> None:
        super().__init__('Identity not found', details={
            'iid': iid
        })
