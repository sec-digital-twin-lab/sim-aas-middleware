import json
import os
from typing import List, Optional, Dict, Tuple, Union

from pydantic import BaseModel

from simaas.core.identity import Identity

from simaas.core.keystore import Keystore

from simaas.core.logging import Logging
from simaas.dor.exceptions import FetchDataObjectFailedError, PushDataObjectFailedError
from simaas.dor.schemas import DataObject, DataObjectRecipe
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.base import P2PProtocol, P2PAddress, p2p_request

logger = Logging.get('dor.protocol')


class LookupRequest(BaseModel):
    obj_ids: List[str]


class LookupResponse(BaseModel):
    records: Dict[str, DataObject]


class P2PLookupDataObject(P2PProtocol):
    NAME = 'dor-lookup'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    async def perform(self, peer: NodeInfo, obj_ids: List[str]) -> Dict[str, DataObject]:
        peer_address = P2PAddress(
            address=peer.p2p_address,
            curve_secret_key=self._node.keystore.curve_secret_key(),
            curve_public_key=self._node.keystore.curve_public_key(),
            curve_server_key=peer.identity.c_public_key
        )

        reply, _ = await p2p_request(
            peer_address, self.NAME, LookupRequest(obj_ids=obj_ids), reply_type=LookupResponse
        )
        reply: LookupResponse = reply  # casting for PyCharm

        return reply.records

    async def handle(
            self, request: LookupRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None
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


class P2PFetchDataObject(P2PProtocol):
    NAME = 'dor-fetch'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    async def perform(self, peer: NodeInfo, obj_id: str, meta_path: str, content_path: str,
                      user_iid: str = None, user_signature: str = None) -> DataObject:
        peer_address = P2PAddress(
            address=peer.p2p_address,
            curve_secret_key=self._node.keystore.curve_secret_key(),
            curve_public_key=self._node.keystore.curve_public_key(),
            curve_server_key=peer.identity.c_public_key
        )

        message = FetchRequest(obj_id=obj_id, user_iid=user_iid, user_signature=user_signature)

        reply, _ = await p2p_request(
            peer_address, self.NAME, message, reply_type=FetchResponse, download_path=content_path
        )
        reply: FetchResponse = reply  # casting for PyCharm

        if reply.successful:
            # store the meta information
            with open(meta_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(reply.meta.model_dump(), f, indent=2)

            return reply.meta

        else:
            raise FetchDataObjectFailedError(details=reply.details)

    async def handle(
            self, request: FetchRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None
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
                return FetchResponse(
                    successful=False, meta=None, details={
                        'reason': 'user id not found',
                        'user_iid': request.user_iid,
                        'obj_id': request.obj_id
                    }
                ), None

            # check if the user has permission to access this data object
            if user.id not in meta.access:
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
                return FetchResponse(
                    successful=False, meta=None, details={
                        'reason': 'authorisation failed',
                        'user_iid': request.user_iid,
                        'obj_id': request.obj_id,
                        'token': token.decode('utf-8'),
                        'signature': request.user_signature
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
    tags: Optional[Dict[str, Union[str, int, float, bool, List, Dict]]]


class PushResponse(BaseModel):
    successful: bool
    meta: Optional[DataObject]
    details: Optional[Dict]


class P2PPushDataObject(P2PProtocol):
    NAME = 'dor-push'

    def __init__(self, node) -> None:
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    async def perform(
            cls, p2p_address: str, keystore: Keystore, peer: Identity, content_path: str,
            data_type: str, data_format: str, owner_iid: str, creators_iid: List[str],
            access_restricted: bool, content_encrypted: bool, license: DataObject.License,
            recipe: Optional[DataObjectRecipe] = None,
            tags: Optional[Dict[str, Union[str, int, float, bool, List, Dict]]] = None
    ) -> DataObject:
        peer_address = P2PAddress(
            address=p2p_address,
            curve_secret_key=keystore.curve_secret_key(),
            curve_public_key=keystore.curve_public_key(),
            curve_server_key=peer.c_public_key
        )

        message = PushRequest(
            owner_iid=owner_iid,
            creators_iid=creators_iid,
            data_type=data_type,
            data_format=data_format,
            access_restricted=access_restricted,
            content_encrypted=content_encrypted,
            license=license,
            recipe=recipe,
            tags=tags
        )

        reply: Tuple[Optional[BaseModel], Optional[str]] = await p2p_request(
            peer_address, cls.NAME, message, reply_type=PushResponse, attachment_path=content_path
        )
        reply: PushResponse = reply[0]  # casting for PyCharm

        if reply.successful:
            return reply.meta

        else:
            raise PushDataObjectFailedError(details=reply.details)

    async def handle(
            self, request: PushRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # does the node have a DOR?
        if self._node.dor is None:
            return PushResponse(
                successful=False, meta=None, details={
                    'reason': 'Target node does not support DOR capabilities',
                    'node_iid': self._node.identity.id
                }
            ), None

        # add the data object
        meta = self._node.dor.add(
            attachment_path, request.data_type, request.data_format, request.owner_iid,
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
