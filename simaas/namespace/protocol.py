import asyncio
import os.path
import traceback

import canonicaljson
from typing import Optional, Tuple, Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from pydantic import BaseModel
from simaas.rti.exceptions import RTIException

from simaas.p2p.exceptions import PeerUnavailableError

from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.exceptions import SaaSRuntimeException, ExceptionContent
from simaas.core.logging import Logging
from simaas.p2p.base import P2PProtocol, P2PAddress, p2p_request

logger = Logging.get('dor.protocol')


def serialise(obj: Any) -> Any:
    """Recursively serialises Pydantic models while preserving primitives."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    elif isinstance(obj, list):
        return [serialise(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: serialise(value) for key, value in obj.items()}
    return obj  # Return primitives unchanged


class NamespaceAuthorisation(BaseModel):
    iid: str
    signature: str


class NamespaceServiceRequest(BaseModel):
    service: str
    method: str
    args: Optional[dict]
    authorisation: NamespaceAuthorisation


class NamespaceServiceResponse(BaseModel):
    result: Optional[Any]
    exception: Optional[ExceptionContent]


def generate_authorised_request(
        authority: Keystore, service: str, method: str, args: Optional[dict]
) -> NamespaceServiceRequest:
    # create the auth token
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(service.encode('utf-8'))
    digest.update(method.encode('utf-8'))
    if args is not None:
        digest.update(canonicaljson.encode_canonical_json(args))
    token = digest.finalize()

    return NamespaceServiceRequest(
        service=service,
        method=method,
        args=args,
        authorisation=NamespaceAuthorisation(
            iid=authority.identity.id,
            signature=authority.sign(token)
        )
    )


def verify_request_authorisation(identity: Identity, request: NamespaceServiceRequest) -> bool:
    # hash the contents
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(request.service.encode('utf-8'))
    digest.update(request.method.encode('utf-8'))
    if request.args is not None:
        digest.update(canonicaljson.encode_canonical_json(request.args))
    token = digest.finalize()

    # verify the signature
    return identity.verify(token, request.authorisation.signature)


class P2PNamespaceServiceCall(P2PProtocol):
    NAME = 'namespace-service-call'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    def _check_restrictions(self, service, method, args, identity: Identity) -> None:
        cls = service.__class__  # Get actual class if method is bound

        # Iterate over the class and its parent classes (including ABCs)
        for base_cls in cls.__mro__:  # Method Resolution Order (MRO) includes all parents
            interface_method = getattr(base_cls, method.__name__, None)
            if interface_method:
                # Check for restriction flags and append appropriate dependencies
                if getattr(interface_method, "_dor_requires_ownership", False):
                    obj_id: str = args['obj_id']
                    self._node.check_dor_ownership(obj_id, identity)

                if getattr(interface_method, "_dor_requires_access", False):
                    obj_id: str = args['obj_id']
                    self._node.check_dor_has_access(obj_id, identity)

                if getattr(interface_method, "_rti_requires_tasks_supported", False):
                    for task in args['tasks']:
                        self._node.check_rti_is_deployed(task.proc_id)
                        self._node.check_rti_not_busy(task.proc_id)

                if getattr(interface_method, "_rti_requires_proc_deployed", False):
                    proc_id: str = args['proc_id']
                    self._node.check_rti_is_deployed(proc_id)

                if getattr(interface_method, "_rti_node_ownership_if_strict", False):
                    if self._node.rti.strict_deployment:
                        self._node.check_rti_node_owner(identity)

                if getattr(interface_method, "_rti_job_or_node_ownership", False):
                    job_id: str = args['job_id']
                    self._node.check_rti_job_or_node_owner(job_id, identity)

                if getattr(interface_method, "_rti_batch_or_node_ownership", False):
                    batch_id: str = args['batch_id']
                    self._node.check_rti_batch_or_node_owner(batch_id, identity)

                if getattr(interface_method, "_rti_requires_proc_not_busy", False):
                    proc_id: str = args['proc_id']
                    self._node.check_rti_not_busy(proc_id)

    @classmethod
    async def perform(
            cls, peer_address: P2PAddress, authority: Keystore, service: str, method: str,
            args: Optional[dict] = None, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            max_attempts: int = 3
    ) -> Optional[Any]:
        for attempt in range(max_attempts):
            try:
                # generate an authorised request
                request = generate_authorised_request(authority, service, method, args)

                # send the request
                reply, _ = await p2p_request(
                    peer_address, cls.NAME, request, reply_type=NamespaceServiceResponse,
                    attachment_path=attachment_path,
                    download_path=download_path
                )
                reply: NamespaceServiceResponse = reply  # casting for PyCharm

                # check if there was an exception
                if reply.exception is not None:
                    raise SaaSRuntimeException(
                        reason=reply.exception.reason,
                        details=reply.exception.details,
                        eid=reply.exception.id
                    )

                return reply.result

            except PeerUnavailableError:
                delay = attempt + 1
                logger.warning(f"Failed for perform namespace service call ({attempt+1}/{max_attempts}) -> "
                               f"Trying again in {delay} seconds...")
                await asyncio.sleep(delay)

        raise RTIException(f"Namespace service call failed after {max_attempts} attempts")


    async def handle(
            self, request: NamespaceServiceRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:

        try:
            # do we know the identity that authorised the request?
            identity = self._node.db.get_identity(request.authorisation.iid)
            if identity is None:
                raise SaaSRuntimeException(reason="Namespace request authorisation failed: identity unknown", details={
                    'request': request.model_dump()
                })

            # is the authorisation valid?
            if not verify_request_authorisation(identity, request):
                raise SaaSRuntimeException(reason="Namespace request authorisation failed: invalid signature", details={
                    'request': request.model_dump()
                })

            # get the service and check restrictions (if any)
            if request.service == 'dor':
                service = self._node.dor
            elif request.service == 'rti':
                service = self._node.rti
            else:
                raise SaaSRuntimeException(reason=f"Invalid service '{request.service}'")

            # get the method
            method = getattr(service, request.method, None)
            if not method:
                raise AttributeError(f"Method '{request.method}' not found on {service.__class__.__name__}")

            # Convert Pydantic models if needed before calling the method
            args = request.args if request.args else {}
            for key, value in args.items():
                if hasattr(method, "__annotations__"):
                    expected_type = method.__annotations__.get(key)

                    # If it's a Pydantic model, convert the dict to an instance
                    if isinstance(value, dict) and isinstance(expected_type, type) and \
                            issubclass(expected_type, BaseModel):
                        args[key] = expected_type(**value)

                    # If it's a list of Pydantic models, convert each dict in the list
                    elif isinstance(value, list) and \
                            hasattr(expected_type, "__origin__") and expected_type.__origin__ is list:
                        item_type = expected_type.__args__[0]
                        if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                            args[key] = [item_type(**item) for item in value]  # Convert list of dicts to list of models

            # do we have an attachment?
            if attachment_path is not None:
                for key, value in args.items():
                    if isinstance(value, str) and value == '###ATTACHMENT###':
                        args[key] = attachment_path

            # check restrictions
            self._check_restrictions(service, method, args, identity)

            # are we expected to send an attachment back?
            content_path = None
            for key, value in args.items():
                if isinstance(value, str) and value == '###REPLY_ATTACHMENT###':
                    content_path = os.path.join(download_path, 'content')
                    args[key] = content_path

            # call the method
            result = method(**args)
            result = serialise(result)

            # serialise the result
            return NamespaceServiceResponse(result=result, exception=None), content_path

        except SaaSRuntimeException as e:
            return NamespaceServiceResponse(result=None, exception=e.content), None

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            return NamespaceServiceResponse(
                result=None, exception=ExceptionContent(id="unknown", reason=str(e), details=None)
            ), None

    @staticmethod
    def request_type():
        return NamespaceServiceRequest

    @staticmethod
    def response_type():
        return NamespaceServiceResponse
