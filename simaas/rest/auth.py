import json
import os
import time
from typing import List, Any

import canonicaljson
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from fastapi import Request
from simaas.rti.schemas import Task

from simaas.core.identity import Identity
from simaas.core.errors import AuthorisationError


REST_AUTH_DOMAIN = b'simaas-rest-auth:v1:'


def signature_window_seconds() -> int:
    raw = os.environ.get('SIMAAS_SIG_WINDOW_SECONDS')
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 300


def timestamp_within_window(issued_at: int) -> bool:
    return abs(int(time.time()) - issued_at) <= signature_window_seconds()


def canonical_auth_url(url: str) -> str:
    """Stable representation of ``METHOD:URL`` so signer and verifier hash the same bytes.

    Normalises method case, lowercases scheme + host, strips a trailing slash
    from the path (root excepted), and sorts query parameters alphabetically.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    method, _, raw = url.partition(':')
    method = method.upper()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True))) if parts.query else ''
    normalised = urlunsplit((scheme, netloc, path, query, ''))
    return f"{method}:{normalised}"


def verify_authorisation_token(identity: Identity, signature: str, url: str,
                               issued_at: int, body: dict = None) -> bool:
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(REST_AUTH_DOMAIN)
    digest.update(canonical_auth_url(url).encode('utf-8'))
    digest.update(str(issued_at).encode('utf-8'))
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
        required = ('saasauth-iid', 'saasauth-signature', 'saasauth-timestamp')
        if not all(h in request.headers for h in required):
            raise AuthorisationError(
                identity_id='unknown',
                operation='authenticate',
                hint='saasauth header information missing'
            )

        # parse + window-check the timestamp before any DB work
        try:
            issued_at = int(request.headers['saasauth-timestamp'])
        except ValueError:
            raise AuthorisationError(
                identity_id='unknown',
                operation='authenticate',
                hint='saasauth-timestamp not an integer',
            )
        if not timestamp_within_window(issued_at):
            raise AuthorisationError(
                identity_id=request.headers['saasauth-iid'],
                operation='authenticate',
                hint='request signature outside the allowed time window',
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
        if not verify_authorisation_token(identity, signature, f"{request.method}:{request.url}", issued_at, body):
            raise AuthorisationError(
                identity_id=iid,
                operation='verify_signature',
                hint='invalid signature'
            )

        # touch the identity
        self.node.db.touch_identity(identity)

        # expose the verified identity to handlers via request.state
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
