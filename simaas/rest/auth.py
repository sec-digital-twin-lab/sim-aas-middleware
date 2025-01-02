import json

import canonicaljson
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from fastapi import Request

from simaas.core.identity import Identity
from simaas.dor.exceptions import DataObjectNotFoundError
from simaas.dor.schemas import DataObject
from simaas.rest.exceptions import AuthorisationFailedError
from simaas.rti.exceptions import ProcessorNotDeployedError, ProcessorBusyError, ProcessorDeployedError
from simaas.rti.schemas import Processor


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
        identity: Identity = self.node.db.get_identity(iid)
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
        self.node.db.touch_identity(identity)

        return identity, body


class VerifyIsOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, obj_id: str, request: Request):
        identity, body = await VerifyAuthorisation(self.node).__call__(request)

        # get the meta information of the object
        meta = self.node.dor.get_meta(obj_id)
        if meta is None:
            raise DataObjectNotFoundError(obj_id)

        # check if the identity is the owner of that data object
        if meta.owner_iid != identity.id:
            raise AuthorisationFailedError({
                'reason': 'user is not the data object owner',
                'obj_id': obj_id,
                'user_iid': identity.id
            })


class VerifyUserHasAccess:
    def __init__(self, node):
        self.node = node

    async def __call__(self, obj_id: str, request: Request):
        identity, body = await VerifyAuthorisation(self.node).__call__(request)

        # get the meta information of the object
        meta: DataObject = self.node.dor.get_meta(obj_id)
        if meta is None:
            raise AuthorisationFailedError({
                'reason': 'data object does not exist',
                'obj_id': obj_id
            })

        # check if the identity has access to the data object content
        if meta.access_restricted and identity.id not in meta.access:
            raise AuthorisationFailedError({
                'reason': 'user has no access to the data object content',
                'obj_id': obj_id,
                'user_iid': identity.id
            })


class VerifyProcessorDeployed:
    def __init__(self, node):
        self.node = node

    async def __call__(self, proc_id: str):
        if not self.node.rti.is_deployed(proc_id):
            raise ProcessorNotDeployedError({
                'proc_id': proc_id
            })


class VerifyProcessorNotDeployed:
    def __init__(self, node):
        self.node = node

    async def __call__(self, proc_id: str):
        if self.node.rti.is_deployed(proc_id):
            raise ProcessorDeployedError({
                'proc_id': proc_id
            })


class VerifyProcessorNotBusy:
    def __init__(self, node):
        self.node = node

    async def __call__(self, proc_id: str):
        proc: Processor = self.node.rti.get_proc(proc_id)
        if proc.state in [Processor.State.BUSY_DEPLOY, Processor.State.BUSY_UNDEPLOY]:
            raise ProcessorBusyError({
                'proc_id': proc_id
            })


class VerifyUserIsJobOwnerOrNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, job_id: str, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)

        # get the job user (i.e., owner) and check if the caller user ids check out
        job_owner_iid = self.node.rti.get_job_owner_iid(job_id)
        if job_owner_iid != identity.id and identity.id != self.node.identity.id:
            raise AuthorisationFailedError({
                'reason': 'user is not the job owner or the node owner',
                'job_id': job_id,
                'job_owner_iid': job_owner_iid,
                'request_user_iid': identity.id,
                'node_iid': self.node.identity.id
            })


class VerifyUserIsNodeOwner:
    def __init__(self, node):
        self.node = node

    async def __call__(self, request: Request):
        identity, _ = await VerifyAuthorisation(self.node).__call__(request)

        # check if the user is the owner of the node
        if self.node.identity.id != identity.id:
            raise AuthorisationFailedError({
                'reason': 'User is not the node owner',
                'user_iid': identity.id,
                'node_iid': self.node.identity.id
            })
