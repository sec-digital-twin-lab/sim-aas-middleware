import json
import os
from typing import List, Optional, Dict, Tuple

from pydantic import BaseModel

from simaas.core.logging import Logging
from simaas.dor.exceptions import FetchDataObjectFailedError
from simaas.dor.schemas import DataObject
from simaas.nodedb.schemas import NodeInfo
from simaas.p2p.base import P2PProtocol

logger = Logging.get('dor.protocol')


class LookupRequest(BaseModel):
    obj_ids: List[str]


class LookupResponse(BaseModel):
    records: Dict[str, DataObject]


class P2PLookupDataObject(P2PProtocol):
    def __init__(self, node) -> None:
        super().__init__('dor-lookup', node.keystore)
        self._node = node

    async def perform(self, peer: NodeInfo, obj_ids: List[str]) -> Dict[str, DataObject]:
        reply = await self.send_and_wait(peer, LookupRequest(obj_ids=obj_ids), LookupResponse)
        return reply.records

    async def handle(self, request: LookupRequest) -> Tuple[LookupResponse, Optional[str]]:
        records: Dict[str, DataObject] = {obj_id: self._node.dor.get_meta(obj_id) for obj_id in request.obj_ids}
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
    def __init__(self, node) -> None:
        super().__init__('dor-fetch', node.keystore)
        self._node = node

    async def perform(self, peer: NodeInfo, obj_id: str, meta_path: str, content_path: str,
                      user_iid: str = None, user_signature: str = None) -> DataObject:
        reply: FetchResponse = await self.send_and_wait(
            peer, FetchRequest(obj_id=obj_id, user_iid=user_iid, user_signature=user_signature), FetchResponse,
            download_path=content_path
        )

        if reply.successful:
            # store the meta information
            with open(meta_path, 'w') as f:
                json.dump(reply.meta.dict(), f, indent=2)

            return reply.meta

        else:
            raise FetchDataObjectFailedError(details=reply.details)

    async def handle(self, request: FetchRequest) -> Tuple[FetchResponse, Optional[str]]:
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

        return FetchResponse(successful=True, meta=meta, details=None), content_path

    @staticmethod
    def request_type():
        return FetchRequest

    @staticmethod
    def response_type():
        return FetchResponse
