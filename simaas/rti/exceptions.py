from simaas.core.exceptions import SaaSRuntimeException


class RTIException(SaaSRuntimeException):
    """
    Base exception class used for errors originating in the RTI subsystem.
    """


class ProcessorNotDeployedError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Processor not deployed', details=details)


class ProcessorDeployedError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Processor already deployed', details=details)


class ProcessorBusyError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Processor is busy, try again later.', details=details)


class UnresolvedInputDataObjectsError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('One or more input data object reference cannot be resolved', details=details)


class AccessNotPermittedError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Identity does not have access to data object', details=details)


class MissingUserSignatureError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Missing user signature for access to data object', details=details)


class InputDataObjectMissing(RTIException):
    def __init__(self, obj_name: str, meta_path: str, content_path: str) -> None:
        super().__init__(f"Input data object '{obj_name}' not found", details={
            'meta_path': meta_path,
            'content_path': content_path
        })


class MismatchingDataTypeOrFormatError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Data type/format of processor input and data object do not match', details=details)


class InvalidJSONDataObjectError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Data object JSON content does not comply with schema', details=details)


class DataObjectContentNotFoundError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Content of data object not found', details=details)


class DataObjectOwnerNotFoundError(RTIException):
    def __init__(self, details: dict) -> None:
        super().__init__('Identity of data object owner not found', details=details)
