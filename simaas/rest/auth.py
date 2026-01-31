import json
from typing import List, Any

import canonicaljson
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from fastapi import Request
from simaas.rti.schemas import Task

from simaas.core.identity import Identity
from simaas.rest.exceptions import AuthorisationFailedError


def verify_authorisation_token(identity: Identity, signature: str, url: str, body: dict = None) -> bool:
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())

    digest.update(url.encode('utf-8'))
    if body:
        digest.update(canonicaljson.encode_canonical_json(body))

    token = digest.finalize()
    return identity.verify(token, signature)


class VerifyAuthorisation:
    def __init__(self, node):
        self.node = node

    async def __call__(self, request: Request) -> (Identity, dict):
        # check if there is the required saasauth header information
        if 'saasauth-iid' not in request.headers or 'saasauth-signature' not in request.headers:
            raise AuthorisationFailedError({
                'reason': 'saasauth information missing',
                'header_keys': list(request.headers.keys())
            })

        # check if the node knows about the identity
        iid = request.headers['saasauth-iid']
        try:
            identity: Identity = await self.node.db.get_identity(iid)
        except Exception as e:
            raise AuthorisationFailedError({
                'reason': 'failed to retrieve identity',
                'iid': iid,
                'error': str(e)
            })
        if identity is None:
            raise AuthorisationFailedError({
                'reason': 'unknown identity',
                'iid': iid
            })

        # verify the signature
        signature = request.headers['saasauth-signature']
        body = await request.body()
        body = body.decode('utf-8')
        body = json.loads(body) if body != '' else {}
        if not verify_authorisation_token(identity, signature, f"{request.method}:{request.url}", body):
            raise AuthorisationFailedError({
                'reason': 'invalid signature',
                'iid': iid,
                'signature': signature
            })

        # touch the identity
        await self.node.db.touch_identity(identity)

        return identity, body


class VerifyIsOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, obj_id: str, request: Request):
        identity, body = await VerifyAuthorisation(self.node).__call__(request)
        await self.node.check_dor_ownership(obj_id, identity)


class VerifyUserHasAccess:
    def __init__(self, node):
        self.node = node

    async def __call__(self, obj_id: str, request: Request):
        identity, body = await VerifyAuthorisation(self.node).__call__(request)
        await self.node.check_dor_has_access(obj_id, identity)


class VerifyTasksSupported:
    def __init__(self, node):
        self.node = node

    async def __call__(self, tasks: List[Task]):
        for task in tasks:
            await self.node.check_rti_is_deployed(task.proc_id)
            await self.node.check_rti_not_busy(task.proc_id)


class VerifyProcessorDeployed:
    def __init__(self, node):
        self.node = node

    async def __call__(self, proc_id: str):
        await self.node.check_rti_is_deployed(proc_id)


class VerifyProcessorNotBusy:
    def __init__(self, node):
        self.node = node

    async def __call__(self, proc_id: str):
        await self.node.check_rti_not_busy(proc_id)


class VerifyUserIsJobOwnerOrNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, job_id: str, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)
        await self.node.check_rti_job_or_node_owner(job_id, identity)


class VerifyUserIsBatchOwnerOrNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, batch_id: str, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)
        await self.node.check_rti_batch_or_node_owner(batch_id, identity)


class VerifyUserIsNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)
        await self.node.check_rti_node_owner(identity)


def make_depends(method, node) -> List[Any]:
    result = []

    # Get the class that owns the method
    cls = getattr(method, "__self__", None)
    if cls is not None:
        cls = cls.__class__  # Get actual class if method is bound

    if cls is None:
        return result  # No class found, return empty list

    # Iterate over the class and its parent classes (including ABCs)
    for base_cls in cls.__mro__:  # Method Resolution Order (MRO) includes all parents
        interface_method = getattr(base_cls, method.__name__, None)
        if interface_method:
            # Check for restriction flags and append appropriate dependencies
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

    return None if len(result) == 0 else result
