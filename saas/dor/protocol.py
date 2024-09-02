import os
from typing import List, Optional, Dict, Union, Any

from pydantic import BaseModel

from saas.dor.exceptions import FetchDataObjectFailedError
from saas.core.helpers import write_json_to_file
from saas.core.identity import Identity
from saas.core.logging import Logging
from saas.dor.schemas import DataObject
from saas.p2p.exceptions import AttachmentNotFoundError
from saas.p2p.protocol import P2PProtocol

logger = Logging.get('dor.protocol')


class LookupRequest(BaseModel):
    obj_ids: List[str]


class LookupResponse(BaseModel):
    records: Dict[str, DataObject]


class FetchRequest(BaseModel):
    obj_id: str
    user_iid: Optional[str]
    user_signature: Optional[str]


class FetchResponse(BaseModel):
    successful: bool
    meta: Optional[DataObject]
    details: Optional[Dict]


class DataObjectRepositoryP2PProtocol(P2PProtocol):
    id = "data_object_repository"

    def __init__(self, args: Union[Any, Union[Identity, str]]) -> None:
        from saas.node.base import Node
        if isinstance(args, Node):
            self._node: Node = args
            super().__init__(self._node.identity, self._node.datastore, DataObjectRepositoryP2PProtocol.id, [
                (LookupRequest, self._handle_lookup, LookupResponse),
                (FetchRequest, self._handle_fetch, FetchResponse)
            ])
        else:
            self._node = None
            identity, datastore = args
            super().__init__(identity, datastore, DataObjectRepositoryP2PProtocol.id, [
                (LookupRequest, self._handle_lookup, LookupResponse),
                (FetchRequest, self._handle_fetch, FetchResponse)
            ])

    def lookup(self, peer_address: (str, int), obj_ids: List[str]) -> Dict[str, DataObject]:
        response, _, _ = self.request(peer_address, LookupRequest(obj_ids=obj_ids))
        return response.records

    def _handle_lookup(self, request: LookupRequest, _) -> LookupResponse:
        records = {obj_id: self._node.dor.get_meta(obj_id) for obj_id in request.obj_ids}
        return LookupResponse(records=records)

    def fetch(self, peer_address: (str, int), obj_id: str,
              destination_meta_path: str, destination_content_path: str,
              user_iid: str = None, user_signature: str = None) -> DataObject:

        response, attachment, _ = self.request(peer_address, FetchRequest(obj_id=obj_id, user_iid=user_iid,
                                                                          user_signature=user_signature))

        # was the fetch attempt successful?
        if not response.successful:
            raise FetchDataObjectFailedError(details=response.details)

        # have we received an attachment?
        if not attachment or not os.path.isfile(attachment):
            raise AttachmentNotFoundError({
                'peer_address': peer_address,
                'obj_id': obj_id,
                'user_iid': user_iid,
                'user_signature': user_signature,
                'response': response.dict()
            })

        # write the data object descriptor to the destination path
        write_json_to_file(response.meta.dict(), destination_meta_path)

        # move the data object content to the destination path
        os.rename(attachment, destination_content_path)

        return DataObject.parse_obj(response.meta)

    def _handle_fetch(self, request: FetchRequest, peer: Identity) -> (FetchResponse, str):
        # check if we have that data object
        meta = self._node.dor.get_meta(request.obj_id)
        if not meta:
            return FetchResponse(successful=False, meta=None, details={
                'reason': 'object not found',
                'obj_id': request.obj_id
            })

        # check if the data object access is restricted and (if so) if the user has the required permission
        if meta.access_restricted:
            # get the identity of the user
            user = self._node.db.get_identity(request.user_iid)
            if user is None:
                return FetchResponse(successful=False, meta=None, details={
                    'reason': 'identity of user not found',
                    'user_iid': request.user_iid
                })

            # check if the user has permission to access this data object
            if user.id not in meta.access:
                return FetchResponse(successful=False, meta=None, details={
                    'reason': 'user does not have access',
                    'user_iid': request.user_iid,
                    'obj_id': request.obj_id
                })

            # verify the access request
            token = f"{peer.id}:{request.obj_id}".encode('utf-8')
            if not user.verify(token, request.user_signature):
                return FetchResponse(successful=False, meta=None, details={
                    'reason': 'authorisation failed',
                    'user_iid': request.user_iid,
                    'obj_id': request.obj_id,
                    'token': token.decode('utf-8'),
                    'signature': request.user_signature
                })

        # we should have the data object content in our local DOR
        content_path = self._node.dor.obj_content_path(meta.c_hash)
        if not os.path.isfile(content_path):
            return FetchResponse(successful=False, meta=None, details={
                'reason': 'data object content not found',
                'user_iid': request.user_iid,
                'obj_id': request.obj_id,
                'c_hash': meta.c_hash
            })

        # touch data object
        self._node.dor.touch_data_object(meta.obj_id)

        # if all is good, send a reply with the meta information followed by the data object content as attachment
        return FetchResponse(successful=True, meta=meta, details=None), content_path
