from simaas.core.exceptions import SaaSRuntimeException


class RESTException(SaaSRuntimeException):
    """
    Base exception class used for errors originating in the REST subsystem.
    """


class UnsupportedRESTMethod(RESTException):
    def __init__(self, method: str, route: str) -> None:
        super().__init__('REST method not supported', details={
            'method': method,
            'route': route
        })


class UnsuccessfulRequestError(RESTException):
    def __init__(self, reason: str, exception_id: str = None, details: dict = None) -> None:
        super().__init__(f"Unsuccessful request: {reason}", id=exception_id, details=details)


class UnexpectedHTTPError(RESTException):
    def __init__(self, details: dict) -> None:
        super().__init__('Unexpected HTTP error encountered', details=details)


class UnexpectedContentType(RESTException):
    def __init__(self, details: dict) -> None:
        super().__init__('Unexpected content type', details=details)


class UnsuccessfulConnectionError(RESTException):
    def __init__(self, url: str, details: dict = None) -> None:
        super().__init__(f"Cannot establish connection to '{url}'", details=details)


class AuthorisationFailedError(RESTException):
    def __init__(self, details: dict = None) -> None:
        super().__init__('Authorisation failed', details=details)
