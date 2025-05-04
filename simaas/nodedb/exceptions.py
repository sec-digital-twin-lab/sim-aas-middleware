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

class NamespaceNotFoundError(NodeDBException):
    def __init__(self, namespace: str) -> None:
        super().__init__(f"Namespace '{namespace}' not found", details={})


class ReservationNotFoundError(NodeDBException):
    def __init__(self, namespace: str, reservation_id: str) -> None:
        super().__init__(f"Reservation '{reservation_id}' not found in namespace '{namespace}'", details={})


class ClaimNotFoundError(NodeDBException):
    def __init__(self, namespace: str, job_id: str) -> None:
        super().__init__(f"Resource claim for job '{job_id}' not found in namespace '{namespace}'", details={})

