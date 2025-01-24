from simaas.core.exceptions import SaaSRuntimeException


class DORException(SaaSRuntimeException):
    """
    Base exception class used for errors originating in the DOR subsystem.
    """


class DataObjectNotFoundError(DORException):
    def __init__(self, obj_id) -> None:
        super().__init__('Data object not found', details={
            'obj_id': obj_id
        })


class DataObjectContentNotFoundError(DORException):
    def __init__(self, details: dict) -> None:
        super().__init__('Data object content not found', details=details)


class FetchDataObjectFailedError(DORException):
    def __init__(self, details: dict) -> None:
        super().__init__('Data object could not be fetched', details=details)


class PushDataObjectFailedError(DORException):
    def __init__(self, details: dict) -> None:
        super().__init__('Data object could not be pushed', details=details)
