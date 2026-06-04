import json
import os
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


def _push_attestation_payload(*, target_iid: str, owner_iid: str, data_type: str, data_format: str,
                              creators_iid: List[str], access_restricted: bool,
                              content_encrypted: bool, content_hash: str) -> dict:
    """Build the canonical fields a runner signs to attest a forwarded push.

    Covers the ownership-relevant request shape AND the content hash, so the
    attestation can't be repurposed against a different target, different
    settings, or different content.
    """
    return {
        'target_iid': target_iid,
        'owner_iid': owner_iid,
        'data_type': data_type,
        'data_format': data_format,
        'creators_iid': sorted(creators_iid),
        'access_restricted': access_restricted,
        'content_encrypted': content_encrypted,
        'content_hash': content_hash,
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
                        'reason': 'user id not found',
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
                        'reason': 'user does not have access',
                        'user_iid': request.user_iid,
                        'obj_id': request.obj_id
                    }
                ), None

            # verify the access request
            token = f"{user.id}:{request.obj_id}".encode('utf-8')
            if not user.verify(token, request.user_signature):
                # Audit-log the failed signature: an invalid signature for an
                # otherwise-allowed user is a sign of an attempted access by
                # someone who doesn't hold the user's private key. Don't echo
                # the token or the failing signature back over the wire — the
                # caller doesn't need them and they help nobody but an attacker.
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
            # owner_iid was previously a separate field on the wire; ownership
            # is now derived server-side from the verified signer (direct push)
            # or from the embedded relay attestation (forwarded push).
            owner_iid: Optional[str] = None,
            # Set by ``P2PRelayPushDataObject.handle`` when this is a forwarded
            # push — the runner's signed attestation telling the target that
            # the immediate mTLS sender (the relay) is acting on the runner's
            # behalf. Direct callers leave this ``None``.
            attestation: Optional[RelayAttestation] = None,
    ) -> DataObject:
        peer_address = P2PAddress(
            address=p2p_address,
            peer_tls_cert=peer.tls_cert
        )

        message = PushRequest(
            owner_iid=keystore.identity.id,  # ignored by server; kept for schema compatibility
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

        # Default: ownership is the mTLS-verified immediate sender. If a relay
        # attestation is present and valid, it overrides — the immediate sender
        # is a relay acting on behalf of the attester named in the attestation.
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
            expected_payload = _push_attestation_payload(
                target_iid=self._node.identity.id,
                owner_iid=request.attestation.iid,
                data_type=request.data_type,
                data_format=request.data_format,
                creators_iid=request.creators_iid,
                access_restricted=request.access_restricted,
                content_encrypted=request.content_encrypted,
                content_hash=content_hash,
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
            # owner_iid was previously a separate wire field; ownership is now
            # derived server-side from the verified signer (direct push) or the
            # attester (relay push). The keystore identity is the source of truth.
            owner_iid: Optional[str] = None,
    ) -> DataObject:
        peer_address = P2PAddress(
            address=custodian_p2p_address,
            peer_tls_cert=custodian_identity.tls_cert
        )

        # Build the runner-signed attestation that the custodian forwards to the
        # target. Binds the attested owner+target+content; the target verifies
        # it against the runner's known identity and records owner = runner.
        attestation_payload = _push_attestation_payload(
            target_iid=target_iid,
            owner_iid=keystore.identity.id,
            data_type=data_type,
            data_format=data_format,
            creators_iid=creators_iid,
            access_restricted=access_restricted,
            content_encrypted=content_encrypted,
            content_hash=hash_file_content(content_path).hex(),
        )
        attestation = sign_relay_attestation(keystore, attestation_payload)

        message = RelayPushRequest(
            target_iid=target_iid,
            owner_iid=keystore.identity.id,  # ignored server-side; kept for schema compat
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
        # The verified mTLS sender (runner) is the rightful owner of this object.
        # For the target == self path we record ownership directly here. For the
        # forward path the target instead reads it from the relay attestation
        # that the runner included in this request.
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

        # forward via the existing push protocol using OUR keystore (the runner cannot reach this peer).
        # The runner's attestation is carried verbatim so the target can record
        # ownership as the runner, not as us (the relay).
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
