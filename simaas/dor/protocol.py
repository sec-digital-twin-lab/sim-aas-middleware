import json
import os
import time
from typing import List, Optional, Dict, Tuple

from pydantic import BaseModel

from simaas.core.identity import Identity

from simaas.core.helpers import hash_file_content
from simaas.core.keystore import Keystore

from simaas.core.logging import get_logger
from simaas.core.errors import NetworkError
from simaas.decorators import p2p_public_access, p2p_requires_authentication
from simaas.dor.schemas import DataObject, DataObjectRecipe, TagValueType
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.base import (
    P2PProtocol, P2PAddress, p2p_request, RelayAttestation,
    sign_relay_attestation, verify_relay_attestation,
)
from simaas.rest.auth import timestamp_within_window


_DOR_FETCH_DOMAIN = b'simaas-dor-fetch:v1:'


def dor_fetch_token(user_iid: str, obj_id: str) -> bytes:
    """Token a user signs to assert access to a restricted DOR object."""
    return _DOR_FETCH_DOMAIN + f"{user_iid}:{obj_id}".encode('utf-8')


def _push_attestation_payload(*, target_iid: str, owner_iid: str, data_type: str, data_format: str,
                              creators_iid: List[str], access_restricted: bool,
                              content_encrypted: bool, content_hash: str,
                              license: 'DataObject.License',
                              recipe: Optional['DataObjectRecipe'],
                              tags: Optional[Dict[str, 'TagValueType']],
                              issued_at: int) -> dict:
    """Canonical fields a runner signs to attest a forwarded push."""
    return {
        'target_iid': target_iid,
        'owner_iid': owner_iid,
        'data_type': data_type,
        'data_format': data_format,
        'creators_iid': sorted(creators_iid),
        'access_restricted': access_restricted,
        'content_encrypted': content_encrypted,
        'content_hash': content_hash,
        'license': license.model_dump(mode='json'),
        'recipe': recipe.model_dump(mode='json') if recipe is not None else None,
        'tags': tags if tags is not None else None,
        'issued_at': issued_at,
    }

log = get_logger('simaas.dor', 'dor')


class LookupRequest(BaseModel):
    obj_ids: List[str]


class LookupResponse(BaseModel):
    records: Dict[str, DataObject]


@p2p_public_access
class P2PLookupDataObject(P2PProtocol):
    NAME = 'dor-lookup'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    def perform(self, peer: NodeInfo, obj_ids: List[str]) -> Dict[str, DataObject]:
        peer_address = P2PAddress(
            address=peer.p2p_address,
            peer_tls_cert=peer.identity.tls_cert
        )

        reply, _ = p2p_request(
            peer_address, self.NAME, LookupRequest(obj_ids=obj_ids), reply_type=LookupResponse
        )
        reply: LookupResponse = reply  # casting for PyCharm

        return reply.records

    def handle(
            self, request: LookupRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # search for the requested data objects and see if we have any of them
        records: Dict[str, DataObject] = {}
        for obj_id in request.obj_ids:
            meta: Optional[DataObject] = self._node.dor.get_meta(obj_id)
            if meta:
                records[obj_id] = meta

        return LookupResponse(records=records), None

    @staticmethod
    def request_type():
        return LookupRequest

    @staticmethod
    def response_type():
        return LookupResponse


class FetchRequest(BaseModel):
    obj_id: str
    user_iid: Optional[str]
    user_signature: Optional[str]


class FetchResponse(BaseModel):
    successful: bool
    meta: Optional[DataObject]
    details: Optional[Dict]


@p2p_public_access
# Access control on restricted objects is enforced inside handle() via the
# request's user_iid + user_signature (a body-level signed token over user.id
# and obj_id). Open lookups for non-restricted objects are intentionally
# allowed without a top-level signed request.
class P2PFetchDataObject(P2PProtocol):
    NAME = 'dor-fetch'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    def perform(self, peer: NodeInfo, obj_id: str, meta_path: str, content_path: str,
                      user_iid: str = None, user_signature: str = None,
                      timeout: Optional[int] = None) -> DataObject:
        peer_address = P2PAddress(
            address=peer.p2p_address,
            peer_tls_cert=peer.identity.tls_cert
        )

        message = FetchRequest(obj_id=obj_id, user_iid=user_iid, user_signature=user_signature)

        reply, _ = p2p_request(
            peer_address, self.NAME, message, reply_type=FetchResponse, download_path=content_path,
            timeout=timeout
        )
        reply: FetchResponse = reply  # casting for PyCharm

        if reply.successful:
            # store the meta information
            with open(meta_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(reply.meta.model_dump(), f, indent=2)

            return reply.meta

        else:
            raise NetworkError(peer_address=peer.p2p_address, operation='fetch', **reply.details)

    def handle(
            self, request: FetchRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # check if we have that data object
        meta = self._node.dor.get_meta(request.obj_id)
        if not meta:
            return FetchResponse(
                successful=False, meta=None, details={
                    'reason': 'data object not found',
                    'obj_id': request.obj_id
                }
            ), None

        # check if the data object access is restricted and (if so) if the user has the required permission
        if meta.access_restricted:
            # get the identity of the user
            user = self._node.db.get_identity(request.user_iid)
            if user is None:
                log.warning('fetch.deny', 'restricted fetch rejected — unknown user identity',
                            obj_id=request.obj_id, user_iid=request.user_iid)
                return FetchResponse(
                    successful=False, meta=None, details={
                        'reason': 'authorisation failed',
                        'user_iid': request.user_iid,
                        'obj_id': request.obj_id
                    }
                ), None

            # check if the user has permission to access this data object
            if user.id not in meta.access:
                log.warning('fetch.deny', 'restricted fetch rejected — user not in ACL',
                            obj_id=request.obj_id, user_iid=request.user_iid)
                return FetchResponse(
                    successful=False, meta=None, details={
                        'reason': 'authorisation failed',
                        'user_iid': request.user_iid,
                        'obj_id': request.obj_id
                    }
                ), None

            # verify the access request
            token = dor_fetch_token(user.id, request.obj_id)
            if not user.verify(token, request.user_signature):
                log.warning('fetch.deny', 'restricted fetch rejected — invalid signature',
                            obj_id=request.obj_id, user_iid=request.user_iid)
                return FetchResponse(
                    successful=False, meta=None, details={
                        'reason': 'authorisation failed',
                        'user_iid': request.user_iid,
                        'obj_id': request.obj_id,
                    }
                ), None

        # we should have the data object content in our local DOR
        content_path = self._node.dor.obj_content_path(meta.c_hash)
        if not os.path.isfile(content_path):
            return FetchResponse(
                successful=False, meta=None, details={
                    'reason': 'data object content not found',
                    'user_iid': request.user_iid,
                    'obj_id': request.obj_id,
                    'c_hash': meta.c_hash
                }
            ), None

        # touch data object
        self._node.dor.touch_data_object(meta.obj_id)

        return (
            FetchResponse(successful=True, meta=meta, details=None),
            content_path
        )

    @staticmethod
    def request_type():
        return FetchRequest

    @staticmethod
    def response_type():
        return FetchResponse


class PushRequest(BaseModel):
    owner_iid: str
    creators_iid: List[str]
    data_type: str
    data_format: str
    access_restricted: bool
    content_encrypted: bool
    license: DataObject.License
    recipe: Optional[DataObjectRecipe]
    tags: Optional[Dict[str, TagValueType]]
    # Set on forwarded pushes (custodian → target). Present means the immediate
    # sender (the relay) is acting on behalf of the attester named in the
    # attestation. The target uses the attested iid as the recorded owner when
    # the attestation verifies and matches this request's content.
    attestation: Optional[RelayAttestation] = None


class RelayPushRequest(PushRequest):
    """Push request that asks the recipient (custodian) to relay-push to ``target_iid``.

    Used by runners whose network position prevents them from reaching the actual target node
    directly (e.g. cloud functions, behind NAT). The custodian — which has full peer connectivity
    by construction — performs the downstream ``P2PPushDataObject`` on the runner's behalf.
    """
    target_iid: str


class RelayPushResponse(BaseModel):
    successful: bool
    meta: Optional[DataObject]
    details: Optional[Dict]


class PushResponse(BaseModel):
    successful: bool
    meta: Optional[DataObject]
    details: Optional[Dict]


@p2p_requires_authentication
class P2PPushDataObject(P2PProtocol):
    NAME = 'dor-push'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(
            cls, p2p_address: str, keystore: Keystore, peer: Identity, content_path: str,
            data_type: str, data_format: str, creators_iid: List[str],
            access_restricted: bool, content_encrypted: bool, license: DataObject.License,
            recipe: Optional[DataObjectRecipe] = None,
            tags: Optional[Dict[str, TagValueType]] = None,
            timeout: Optional[int] = None,
            # owner_iid is ignored on the wire — server derives it from the
            # verified signer (direct push) or the attestation (relay push).
            owner_iid: Optional[str] = None,
            # Set on a relay-forwarded push; lets the target attribute ownership
            # to the attester rather than the immediate (relay) sender.
            attestation: Optional[RelayAttestation] = None,
    ) -> DataObject:
        peer_address = P2PAddress(
            address=p2p_address,
            peer_tls_cert=peer.tls_cert
        )

        message = PushRequest(
            owner_iid=keystore.identity.id,
            creators_iid=creators_iid,
            data_type=data_type,
            data_format=data_format,
            access_restricted=access_restricted,
            content_encrypted=content_encrypted,
            license=license,
            recipe=recipe,
            tags=tags,
            attestation=attestation,
        )

        reply: Tuple[Optional[BaseModel], Optional[str]] = p2p_request(
            peer_address, cls.NAME, message, reply_type=PushResponse, attachment_path=content_path,
            timeout=timeout, with_authorisation_by=keystore,
        )
        reply: PushResponse = reply[0]  # casting for PyCharm

        if reply.successful:
            return reply.meta

        else:
            raise NetworkError(peer_address=p2p_address, operation='push', **reply.details)

    def handle(
            self, request: PushRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # does the node have a DOR?
        if self._node.dor is None:
            return PushResponse(
                successful=False, meta=None, details={
                    'reason': 'Target node does not support DOR capabilities',
                    'node_iid': self._node.identity.id
                }
            ), None

        # Ownership = mTLS-verified sender, unless a valid relay attestation
        # names the attester as the rightful owner instead.
        owner_iid = identity.id
        if request.attestation is not None:
            attester = self._node.db.get_identity(request.attestation.iid)
            if attester is None:
                return PushResponse(
                    successful=False, meta=None, details={
                        'reason': 'attester identity unknown to target',
                        'attester_iid': request.attestation.iid,
                    }
                ), None
            content_hash = hash_file_content(attachment_path).hex() if attachment_path else ''
            if not timestamp_within_window(request.attestation.issued_at):
                return PushResponse(
                    successful=False, meta=None, details={
                        'reason': 'relay attestation outside time window',
                        'attester_iid': request.attestation.iid,
                    }
                ), None
            expected_payload = _push_attestation_payload(
                target_iid=self._node.identity.id,
                owner_iid=request.attestation.iid,
                data_type=request.data_type,
                data_format=request.data_format,
                creators_iid=request.creators_iid,
                access_restricted=request.access_restricted,
                content_encrypted=request.content_encrypted,
                content_hash=content_hash,
                license=request.license,
                recipe=request.recipe,
                tags=request.tags,
                issued_at=request.attestation.issued_at,
            )
            if not verify_relay_attestation(attester, expected_payload, request.attestation.signature):
                return PushResponse(
                    successful=False, meta=None, details={
                        'reason': 'relay attestation does not match this request',
                        'attester_iid': request.attestation.iid,
                    }
                ), None
            owner_iid = attester.id

        # add the data object
        meta = self._node.dor.add(
            attachment_path, request.data_type, request.data_format, owner_iid,
            creators_iid=request.creators_iid, access_restricted=request.access_restricted,
            content_encrypted=request.content_encrypted, license=request.license,
            tags=request.tags, recipe=request.recipe
        )

        return PushResponse(successful=True, meta=meta, details=None), None

    @staticmethod
    def request_type():
        return PushRequest

    @staticmethod
    def response_type():
        return PushResponse


@p2p_requires_authentication
class P2PRelayPushDataObject(P2PProtocol):
    """Custodian-side relay for a runner's data-object push.

    Runners whose network position cannot reach the actual target node directly send the push
    to their custodian — which has full peer connectivity — and the custodian forwards it via
    the existing ``P2PPushDataObject`` protocol.
    """
    NAME = 'dor-relay-push'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(
            cls, custodian_p2p_address: str, keystore: Keystore, custodian_identity: Identity,
            target_iid: str, content_path: str,
            data_type: str, data_format: str, creators_iid: List[str],
            access_restricted: bool, content_encrypted: bool, license: DataObject.License,
            recipe: Optional[DataObjectRecipe] = None,
            tags: Optional[Dict[str, TagValueType]] = None,
            timeout: Optional[int] = None,
            # Ignored on the wire — server-side ownership comes from the
            # verified signer or the relay attestation.
            owner_iid: Optional[str] = None,
    ) -> DataObject:
        peer_address = P2PAddress(
            address=custodian_p2p_address,
            peer_tls_cert=custodian_identity.tls_cert
        )

        attestation_payload = _push_attestation_payload(
            target_iid=target_iid,
            owner_iid=keystore.identity.id,
            data_type=data_type,
            data_format=data_format,
            creators_iid=creators_iid,
            access_restricted=access_restricted,
            content_encrypted=content_encrypted,
            content_hash=hash_file_content(content_path).hex(),
            license=license,
            recipe=recipe,
            tags=tags,
            issued_at=int(time.time()),
        )
        attestation = sign_relay_attestation(keystore, attestation_payload)

        message = RelayPushRequest(
            target_iid=target_iid,
            owner_iid=keystore.identity.id,
            creators_iid=creators_iid,
            data_type=data_type,
            data_format=data_format,
            access_restricted=access_restricted,
            content_encrypted=content_encrypted,
            license=license,
            recipe=recipe,
            tags=tags,
            attestation=attestation,
        )

        reply: Tuple[Optional[BaseModel], Optional[str]] = p2p_request(
            peer_address, cls.NAME, message, reply_type=RelayPushResponse,
            attachment_path=content_path, timeout=timeout, with_authorisation_by=keystore,
        )
        reply: RelayPushResponse = reply[0]

        if reply.successful:
            return reply.meta
        else:
            raise NetworkError(peer_address=custodian_p2p_address, operation='relay-push', **(reply.details or {}))

    def handle(
            self, request: RelayPushRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # target == self: record ownership = mTLS sender directly.
        # target != self: forward + carry the attestation so the target attributes
        # ownership to the runner rather than this relay.
        owner_iid = identity.id

        # find the target node in the local network view (custodian has full peer connectivity)
        network = self._node.db.get_network()
        target_node = next((n for n in network if n.identity.id == request.target_iid), None)
        if target_node is None:
            return RelayPushResponse(
                successful=False, meta=None,
                details={'reason': 'target node not found in network', 'target_iid': request.target_iid}
            ), None
        if not target_node.has_dor():
            return RelayPushResponse(
                successful=False, meta=None,
                details={'reason': 'target node does not support DOR capabilities', 'target_iid': request.target_iid}
            ), None

        # if the target is THIS node, push locally via the DOR add path instead of round-tripping
        if target_node.identity.id == self._node.identity.id:
            if self._node.dor is None:
                return RelayPushResponse(
                    successful=False, meta=None,
                    details={'reason': 'relay node does not have DOR locally', 'target_iid': request.target_iid}
                ), None
            meta = self._node.dor.add(
                attachment_path, request.data_type, request.data_format, owner_iid,
                creators_iid=request.creators_iid, access_restricted=request.access_restricted,
                content_encrypted=request.content_encrypted, license=request.license,
                tags=request.tags, recipe=request.recipe
            )
            return RelayPushResponse(successful=True, meta=meta, details=None), None

        # forward via the existing push protocol using OUR keystore. The
        # attestation is carried verbatim — the target verifies it independently.
        try:
            meta = P2PPushDataObject.perform(
                target_node.p2p_address, self._node.keystore, target_node.identity,
                attachment_path, request.data_type, request.data_format,
                request.creators_iid,
                request.access_restricted, request.content_encrypted, request.license,
                recipe=request.recipe, tags=request.tags,
                attestation=request.attestation,
            )
            return RelayPushResponse(successful=True, meta=meta, details=None), None
        except NetworkError as e:
            return RelayPushResponse(
                successful=False, meta=None,
                details={
                    'reason': f'relay-push to target failed: {e.reason}',
                    'target_iid': request.target_iid,
                    'target_address': target_node.p2p_address,
                }
            ), None

    @staticmethod
    def request_type():
        return RelayPushRequest

    @staticmethod
    def response_type():
        return RelayPushResponse
