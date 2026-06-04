import json
from typing import List, Any

import canonicaljson
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from fastapi import Request
from simaas.rti.schemas import Task

from simaas.core.identity import Identity
from simaas.core.errors import AuthorisationError


def verify_authorisation_token(identity: Identity, signature: str, url: str, body: dict = None) -> bool:
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())

    digest.update(url.encode('utf-8'))
    if body:
        digest.update(canonicaljson.encode_canonical_json(body))

    token = digest.finalize()
    return identity.verify(token, signature)


async def _extract_signed_body(request: Request) -> dict:
    """Return the body dict that the client signed.

    JSON requests: the request body decoded as JSON.
    multipart/form-data requests (file uploads): the JSON-decoded ``body`` form
    field, with the transport-only ``__part_info`` key stripped — the client
    signs the invariant payload once and reuses that signature across all chunks.
    """
    content_type = request.headers.get('content-type', '')
    if content_type.startswith('multipart/form-data'):
        form = await request.form()
        raw = form.get('body')
        if raw is None:
            return {}
        parsed = json.loads(raw)
        parsed.pop('__part_info', None)
        return parsed

    raw = await request.body()
    decoded = raw.decode('utf-8')
    return json.loads(decoded) if decoded else {}


class VerifyAuthorisation:
    def __init__(self, node):
        self.node = node

    async def __call__(self, request: Request) -> (Identity, dict):
        # check if there is the required saasauth header information
        if 'saasauth-iid' not in request.headers or 'saasauth-signature' not in request.headers:
            raise AuthorisationError(
                identity_id='unknown',
                operation='authenticate',
                hint='saasauth header information missing'
            )

        # check if the node knows about the identity
        iid = request.headers['saasauth-iid']
        identity: Identity = self.node.db.get_identity(iid)
        if identity is None:
            raise AuthorisationError(
                identity_id=iid,
                operation='authenticate',
                hint='unknown identity'
            )

        # verify the signature
        signature = request.headers['saasauth-signature']
        body = await _extract_signed_body(request)
        if not verify_authorisation_token(identity, signature, f"{request.method}:{request.url}", body):
            raise AuthorisationError(
                identity_id=iid,
                operation='verify_signature',
                hint='invalid signature'
            )

        # touch the identity
        self.node.db.touch_identity(identity)

        # stash the verified identity for handlers that need it (e.g. rest_add
        # derives owner_iid from the signer rather than trusting the body)
        request.state.identity = identity

        return identity, body


class VerifyIsOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, obj_id: str, request: Request):
        identity, body = await VerifyAuthorisation(self.node).__call__(request)
        self.node.check_dor_ownership(obj_id, identity)


class VerifyUserHasAccess:
    def __init__(self, node):
        self.node = node

    async def __call__(self, obj_id: str, request: Request):
        identity, body = await VerifyAuthorisation(self.node).__call__(request)
        self.node.check_dor_has_access(obj_id, identity)


class VerifyTasksSupported:
    def __init__(self, node):
        self.node = node

    def __call__(self, tasks: List[Task]):
        for task in tasks:
            self.node.check_rti_is_deployed(task.proc_id)
            self.node.check_rti_not_busy(task.proc_id)


class VerifyProcessorDeployed:
    def __init__(self, node):
        self.node = node

    def __call__(self, proc_id: str):
        self.node.check_rti_is_deployed(proc_id)


class VerifyProcessorNotBusy:
    def __init__(self, node):
        self.node = node

    def __call__(self, proc_id: str):
        self.node.check_rti_not_busy(proc_id)


class VerifyUserIsJobOwnerOrNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, job_id: str, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)
        self.node.check_rti_job_or_node_owner(job_id, identity)


class VerifyUserIsBatchOwnerOrNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, batch_id: str, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)
        self.node.check_rti_batch_or_node_owner(batch_id, identity)


class VerifyUserIsNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)
        self.node.check_rti_node_owner(identity)


_AUTH_MARKERS = (
    "_require_authentication",
    "_dor_requires_ownership",
    "_dor_requires_access",
    "_rti_requires_tasks_supported",
    "_rti_requires_proc_deployed",
    "_rti_node_ownership_if_strict",
    "_rti_job_or_node_ownership",
    "_rti_batch_or_node_ownership",
    "_rti_requires_proc_not_busy",
)


def make_depends(method, node) -> List[Any]:
    result = []
    public = False
    auth_marker_present = False  # any @requires_* marker, even if it produces no dep in this mode

    # Get the class that owns the method
    cls = getattr(method, "__self__", None)
    if cls is not None:
        cls = cls.__class__  # Get actual class if method is bound

    if cls is None:
        # No class found — treat as ambiguous; enforce at the route level instead
        raise RuntimeError(
            f"cannot determine owning class for endpoint handler {method!r}; "
            f"every endpoint must carry an auth marker (@requires_*) or @public_access"
        )

    # Walk MRO so a marker on the ABC counts (matches how the decorators are declared).
    for base_cls in cls.__mro__:
        interface_method = getattr(base_cls, method.__name__, None)
        if interface_method is None:
            continue

        if getattr(interface_method, "_public_access", False):
            public = True

        for marker in _AUTH_MARKERS:
            if getattr(interface_method, marker, False):
                auth_marker_present = True
                break

        if getattr(interface_method, "_require_authentication", False):
            result.append(VerifyAuthorisation)

        if getattr(interface_method, "_dor_requires_ownership", False):
            result.append(VerifyIsOwner)

        if getattr(interface_method, "_dor_requires_access", False):
            result.append(VerifyUserHasAccess)

        if getattr(interface_method, "_rti_requires_tasks_supported", False):
            result.append(VerifyTasksSupported)

        if getattr(interface_method, "_rti_requires_proc_deployed", False):
            result.append(VerifyProcessorDeployed)

        if getattr(interface_method, "_rti_node_ownership_if_strict", False):
            if node.rti.strict_deployment:
                result.append(VerifyUserIsNodeOwner)

        if getattr(interface_method, "_rti_job_or_node_ownership", False):
            result.append(VerifyUserIsJobOwnerOrNodeOwner)

        if getattr(interface_method, "_rti_batch_or_node_ownership", False):
            result.append(VerifyUserIsBatchOwnerOrNodeOwner)

        if getattr(interface_method, "_rti_requires_proc_not_busy", False):
            result.append(VerifyProcessorNotBusy)

    if public and auth_marker_present:
        raise RuntimeError(
            f"endpoint handler {cls.__name__}.{method.__name__} is marked both "
            f"@public_access and with one or more @requires_* markers; pick one"
        )

    if not public and not auth_marker_present:
        raise RuntimeError(
            f"endpoint handler {cls.__name__}.{method.__name__} has no auth marker; "
            f"add @public_access if anonymous access is intentional, or one of the "
            f"@requires_* markers from simaas.decorators"
        )

    return None if not result else result
