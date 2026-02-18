import asyncio
import socket
import threading
import time
import traceback
from contextlib import asynccontextmanager
from typing import List, Optional

import uvicorn

from threading import Thread
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from simaas.rest.auth import make_depends
from starlette.responses import JSONResponse

from simaas.core.errors import (
    _BaseError,
    AuthenticationError,
    AuthorisationError,
    NotFoundError,
    ValidationError,
    NetworkError,
)
from simaas.core.logging import get_logger
from simaas.meta import __title__, __version__, __description__
from simaas.rest.schemas import EndpointDefinition

log = get_logger('simaas.rest', 'rest')

DOCS_ENDPOINT_PREFIX = "/api/v1"


def _error_response(e: _BaseError, status_code: int) -> JSONResponse:
    """Create a JSON error response from a _BaseError exception."""
    log.error('error', f'Exception: {e.reason}', id=e.id)
    return JSONResponse(
        status_code=status_code,
        content={
            'reason': e.reason,
            'id': e.id,
            'details': e.details,
        }
    )


class RESTApp:
    def __init__(self, origins: list[str] = None) -> None:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            yield
            await self.close()

        self.api = FastAPI(
            openapi_url=f"{DOCS_ENDPOINT_PREFIX}/openapi.json",
            docs_url=f"{DOCS_ENDPOINT_PREFIX}/docs",
            lifespan=lifespan
        )

        # New error hierarchy handlers (specific exceptions first)
        @self.api.exception_handler(AuthenticationError)
        async def auth_error_handler(_: Request, e: AuthenticationError):
            return _error_response(e, status_code=401)

        @self.api.exception_handler(AuthorisationError)
        async def authz_error_handler(_: Request, e: AuthorisationError):
            return _error_response(e, status_code=403)

        @self.api.exception_handler(NotFoundError)
        async def not_found_handler(_: Request, e: NotFoundError):
            return _error_response(e, status_code=404)

        @self.api.exception_handler(ValidationError)
        async def validation_handler(_: Request, e: ValidationError):
            return _error_response(e, status_code=422)

        @self.api.exception_handler(NetworkError)
        async def network_handler(_: Request, e: NetworkError):
            return _error_response(e, status_code=502)

        @self.api.exception_handler(_BaseError)
        async def base_error_handler(_: Request, e: _BaseError):
            return _error_response(e, status_code=500)

        @self.api.exception_handler(Exception)
        async def generic_exception_handler(_: Request, e: Exception):
            log.error('exception', 'Unexpected exception', exc=e)
            return JSONResponse(
                status_code=500,
                content={
                    'error': 'Internal Server Error',
                    'message': f'An unexpected error occurred: {e}',
                    'trace': ''.join(traceback.format_exception(None, e, e.__traceback__))
                }
            )

        # setup CORS
        self.api.add_middleware(
            CORSMiddleware,
            allow_origins=origins if origins else ['*'],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def register(self, endpoint: EndpointDefinition, dependencies: Optional[List[Depends]]) -> None:
        route = f"{endpoint.prefix}/{endpoint.rule}"
        log.info('register', 'Mapping route to function', method=endpoint.method, route=route)
        if endpoint.method in ['POST', 'GET', 'PUT', 'DELETE']:
            self.api.add_api_route(route,
                                   endpoint.function,
                                   methods=[endpoint.method],
                                   response_model=endpoint.response_model,
                                   dependencies=dependencies,
                                   description=endpoint.function.__doc__)
        else:
            raise ValidationError(
                field='http_method',
                expected='POST, GET, PUT, or DELETE',
                actual=endpoint.method,
                hint=f"Route: {route}"
            )

    async def close(self) -> None:
        log.info('shutdown', 'REST app is shutting down')


class RESTService:
    def __init__(self, node, host: str, port: int, bind_all_address: bool) -> None:
        self._node = node
        self._host = host
        self._port = port
        self._app = RESTApp()
        self._thread = None
        self._bind_all_address = bind_all_address
        self._ready_event = threading.Event()
        self._server: Optional[uvicorn.Server] = None
        self._shutdown_event = threading.Event()

    def is_ready(self) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            return False

    def address(self) -> (str, int):
        return self._host, self._port

    def add(self, endpoints: list[EndpointDefinition]) -> None:
        for endpoint in endpoints:
            # determine dependencies
            dependencies = make_depends(endpoint.function, self._node)
            dependencies = [Depends(d(self._node)) for d in dependencies] if dependencies else None

            self._app.register(endpoint, dependencies)

        # update the openapi schema
        self._app.api.openapi_schema = get_openapi(
            title=__title__,
            version=__version__,
            description=__description__,
            routes=self._app.api.routes
        )

    def start_service(self) -> None:
        if self._thread is None:
            log.info('startup', 'REST service starting up')

            # Reset shutdown event for clean start
            self._shutdown_event.clear()

            config = uvicorn.Config(
                self._app.api,
                host=self._host if not self._bind_all_address else "0.0.0.0",
                port=self._port,
                log_level="info"
            )
            self._server = uvicorn.Server(config)

            def _run_server():
                asyncio.run(self._server.serve())
                self._shutdown_event.set()

            self._thread = Thread(target=_run_server, daemon=True)
            self._thread.start()

            # spawn a checker thread to set ready event when service is up
            def _check_ready():
                while not self.is_ready():
                    time.sleep(0.1)
                self._ready_event.set()
            Thread(target=_check_ready, daemon=True).start()

        else:
            log.warning('startup', 'REST service thread already exists, ignoring start request')

    def stop_service(self) -> None:
        if self._thread is None:
            log.warning('shutdown', 'REST service thread does not exist, ignoring stop request')

        else:
            log.info('shutdown', 'REST service shutting down')
            self._ready_event.clear()

            # signal uvicorn server to shutdown
            if self._server is not None:
                self._server.should_exit = True

                # wait for server to actually stop (with timeout)
                self._shutdown_event.wait(timeout=5.0)

            self._thread = None
            self._server = None

    async def wait_until_ready(self, timeout: float = 10.0) -> bool:
        """Wait until the REST service is ready.

        Returns True if service is ready, False if timeout occurred.
        """
        start = asyncio.get_event_loop().time()
        while not self._ready_event.is_set():
            if asyncio.get_event_loop().time() - start > timeout:
                return False
            await asyncio.sleep(0.1)
        return True
