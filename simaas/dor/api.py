from __future__ import annotations

import abc
from typing import Optional, List, Tuple, Dict, Union
from fastapi import Response, Form, UploadFile, File

from simaas.dor.schemas import DORStatistics, DataObjectProvenance, DataObject, SearchParameters, DataObjectRecipe
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.rest.proxy import EndpointProxy, Session, get_proxy_prefix
from simaas.rest.schemas import EndpointDefinition
from simaas.decorators import requires_ownership, requires_access

DOR_ENDPOINT_PREFIX = "/api/v1/dor"


class DORInterface(abc.ABC):
    @abc.abstractmethod
    def search(
            self, patterns: Optional[List[str]] = None, owner_iid: Optional[str] = None,
            data_type: Optional[str] = None, data_format: Optional[str] = None, c_hashes: Optional[List[str]] = None
    ) -> List[DataObject]:
        """
        Searches a DOR for data objects that match the search criteria. There are two kinds of criteria: constraints
        and patterns. Search constraints are conjunctive, i.e., all constraints have to be matched in order for a data
        objective to be considered for inclusion in the search result. Constraints include: `owner_iid`, `data_type`,
        `data_format` or list of `c_hashes`. After applying the search constraints, the result set is further filtered
        by the search patterns. Unlike constraints, search patterns are disjunctive, i.e., so long as any of the
        patterns is matched, the data object is included in the final result set. Search patterns are applied to the
        data object tags only. A search pattern is considered matched if it is a substring of either tag key or value.
        """

    @abc.abstractmethod
    def statistics(self) -> DORStatistics:
        """
        Retrieves some statistics from the DOR. This includes a list of all data types and formats found in the DOR.
        """

    @abc.abstractmethod
    def add(
            self, content_path: str, data_type: str, data_format: str, owner_iid: str,
            creators_iid: Optional[List[str]] = None, access_restricted: Optional[bool] = False,
            content_encrypted: Optional[bool] = False, license: Optional[DataObject.License] = None,
            tags: Optional[Dict[str, Union[str, int, float, bool, List, Dict]]] = None,
            recipe: Optional[DataObjectRecipe] = None
    ) -> DataObject:
        """
        Adds a new content data object to the DOR and returns the meta information for this data object. There is no
        restriction as to the nature or size of the content.
        """

    @abc.abstractmethod
    @requires_ownership
    def remove(self, obj_id: str) -> Optional[DataObject]:
        """
        Deletes a data object from the DOR and returns the meta information of that data object. Authorisation by the
        data object owner is required.
        """

    @abc.abstractmethod
    def get_meta(self, obj_id: str) -> Optional[DataObject]:
        """
        Retrieves the meta information of a data object. Depending on the type of the data object, either a
        `CDataObject` or a `GPPDataObject` is returned, providing meta information for content and GPP data objects,
        respectively.
        """


    @abc.abstractmethod
    @requires_access
    def get_content(self, obj_id: str, content_path: str) -> None:
        ...


    @abc.abstractmethod
    def get_provenance(self, c_hash: str) -> Optional[DataObjectProvenance]:
        """
        Retrieves the provenance information of a data object (identified by its content hash `c_hash`). Provenance
        data includes detailed information how the content of a data object has been produced. In principle, this
        information enables users to reproduce the contents by repeating the exact same steps. Note that it is possible
        that there are multiple routes by which a content can be generated. Depending on the use case, this kind of
        situation is likely to be rare. However, careful analysis of the provenance information might be needed to
        understand how the content has been created.
        """

    @abc.abstractmethod
    @requires_ownership
    def grant_access(self, obj_id: str, user_iid: str) -> DataObject:
        """
        Grants a user the right to access the contents of a restricted data object. Authorisation required by the owner
        of the data object. Note that access rights only matter if the data object has access restrictions.
        """

    @abc.abstractmethod
    @requires_ownership
    def revoke_access(self, obj_id: str, user_iid: str) -> DataObject:
        """
        Revokes the right to access the contents of a restricted data object from a user. Authorisation required by the
        owner of the data object. Note that access rights only matter if the data object has access restrictions.
        """

    @abc.abstractmethod
    @requires_ownership
    def transfer_ownership(self, obj_id: str, new_owner_iid: str) -> DataObject:
        """
        Transfers the ownership of a data object to another user. Authorisation required by the current owner of the
        data object.
        """

    @abc.abstractmethod
    @requires_ownership
    def update_tags(self, obj_id: str, tags: List[DataObject.Tag]) -> DataObject:
        """
        Adds tags to a data object or updates tags in case they already exist. Authorisation required by the owner of
        the data object.
        """

    @abc.abstractmethod
    @requires_ownership
    def remove_tags(self, obj_id: str, keys: List[str]) -> DataObject:
        """
        Removes tags from a data object. Authorisation required by the owner of the data object.
        """


class DORRESTService(DORInterface):
    @abc.abstractmethod
    def rest_add(self, body: str = Form(...), attachment: UploadFile = File(...)) -> DataObject:
        """
        Adds a new content data object to the DOR and returns the meta information for this data object. The content
        of the data object itself is uploaded as an attachment (binary). There is no restriction as to the nature or
        size of the content.
        """

    @abc.abstractmethod
    @requires_access
    def rest_get_content(self, obj_id: str) -> Response:
        """
        Retrieves the content of a data object. Authorisation required by a user who has been granted access to the
        data object.
        """

    def rest_search(self, p: SearchParameters) -> List[DataObject]:
        """
        Searches a DOR for data objects that match the search criteria. There are two kinds of criteria: constraints
        and patterns. Search constraints are conjunctive, i.e., all constraints have to be matched in order for a data
        objective to be considered for inclusion in the search result. Constraints include: `owner_iid`, `data_type`,
        `data_format` or list of `c_hashes`. After applying the search constraints, the result set is further filtered
        by the search patterns. Unlike constraints, search patterns are disjunctive, i.e., so long as any of the
        patterns is matched, the data object is included in the final result set. Search patterns are applied to the
        data object tags only. A search pattern is considered matched if it is a substring of either tag key or value.
        """
        return self.search(
            patterns=p.patterns, owner_iid=p.owner_iid, data_type=p.data_type, data_format=p.data_format,
            c_hashes=p.c_hashes
        )

    def endpoints(self) -> List[EndpointDefinition]:
        return [
            EndpointDefinition('GET', DOR_ENDPOINT_PREFIX, '', self.rest_search, List[DataObject]),
            EndpointDefinition('GET', DOR_ENDPOINT_PREFIX, 'statistics', self.statistics, DORStatistics),
            EndpointDefinition('POST', DOR_ENDPOINT_PREFIX, 'add', self.rest_add, Optional[DataObject]),
            EndpointDefinition('DELETE', DOR_ENDPOINT_PREFIX, '{obj_id}', self.remove, DataObject),
            EndpointDefinition('GET', DOR_ENDPOINT_PREFIX, '{obj_id}/meta', self.get_meta, Optional[DataObject]),
            EndpointDefinition('GET', DOR_ENDPOINT_PREFIX, '{obj_id}/content', self.rest_get_content, None),
            EndpointDefinition('GET', DOR_ENDPOINT_PREFIX, '{c_hash}/provenance', self.get_provenance, Optional[DataObjectProvenance]),
            EndpointDefinition('POST', DOR_ENDPOINT_PREFIX, '{obj_id}/access/{user_iid}', self.grant_access, DataObject),
            EndpointDefinition('DELETE', DOR_ENDPOINT_PREFIX, '{obj_id}/access/{user_iid}', self.revoke_access, DataObject),
            EndpointDefinition('PUT', DOR_ENDPOINT_PREFIX, '{obj_id}/owner/{new_owner_iid}', self.transfer_ownership, DataObject),
            EndpointDefinition('PUT', DOR_ENDPOINT_PREFIX, '{obj_id}/tags', self.update_tags, DataObject),
            EndpointDefinition('DELETE', DOR_ENDPOINT_PREFIX, '{obj_id}/tags', self.remove_tags, DataObject),
        ]


class DORProxy(EndpointProxy):
    @classmethod
    def from_session(cls, session: Session) -> DORProxy:
        return DORProxy(remote_address=session.address, credentials=session.credentials,
                        endpoint_prefix=(session.endpoint_prefix_base, 'dor'))

    def __init__(self, remote_address: (str, int), credentials: (str, str) = None,
                 endpoint_prefix: Tuple[str, str] = get_proxy_prefix(DOR_ENDPOINT_PREFIX)):
        super().__init__(endpoint_prefix, remote_address, credentials=credentials)

    def search(self, patterns: list[str] = None, owner_iid: str = None,
               data_type: str = None, data_format: str = None,
               c_hashes: list[str] = None) -> List[DataObject]:
        body = {
            'patterns': patterns if patterns is not None and len(patterns) > 0 else None,
            'owner_iid': owner_iid,
            'data_type': data_type,
            'data_format': data_format,
            'c_hashes': c_hashes
        }

        results = self.get('', body=body)
        return [DataObject.model_validate(result) for result in results]

    def statistics(self) -> DORStatistics:
        result = self.get('statistics')
        return DORStatistics.model_validate(result)

    def add_data_object(self, content_path: str, owner: Identity, access_restricted: bool, content_encrypted: bool,
                        data_type: str, data_format: str, creators: List[Identity] = None, recipe: dict = None,
                        tags: List[DataObject.Tag] = None, license_by: bool = False, license_sa: bool = False,
                        license_nc: bool = False, license_nd: bool = False) -> DataObject:
        body = {
            'owner_iid': owner.id,
            'creators_iid': [creator.id for creator in creators] if creators else [owner.id],
            'data_type': data_type,
            'data_format': data_format,
            'access_restricted': access_restricted,
            'content_encrypted': content_encrypted,
            'license': {
                'by': license_by,
                'sa': license_sa,
                'nc': license_nc,
                'nd': license_nd
            },
            'recipe': recipe if recipe else None,
            'tags': {tag.key: tag.value for tag in tags} if tags else None
        }

        result = self.post('add', body=body, attachment_path=content_path)
        return DataObject.model_validate(result)

    def delete_data_object(self, obj_id: str, with_authorisation_by: Keystore) -> Optional[DataObject]:
        result = self.delete(f"{obj_id}", with_authorisation_by=with_authorisation_by)
        return DataObject.model_validate(result) if result else None

    def get_meta(self, obj_id: str) -> Optional[DataObject]:
        result = self.get(f"{obj_id}/meta")
        return DataObject.model_validate(result) if result else None

    def get_content(self, obj_id: str, with_authorisation_by: Keystore, download_path: str) -> None:
        self.get(f"{obj_id}/content", download_path=download_path, with_authorisation_by=with_authorisation_by)

    def get_provenance(self, c_hash: str) -> DataObjectProvenance:
        result = self.get(f"{c_hash}/provenance")
        return DataObjectProvenance.model_validate(result)

    def grant_access(self, obj_id: str, authority: Keystore, identity: Identity) -> DataObject:
        result = self.post(f"{obj_id}/access/{identity.id}", with_authorisation_by=authority)
        return DataObject.model_validate(result)

    def revoke_access(self, obj_id: str, authority: Keystore, identity: Identity) -> DataObject:
        result = self.delete(f"{obj_id}/access/{identity.id}", with_authorisation_by=authority)
        return DataObject.model_validate(result)

    def transfer_ownership(self, obj_id: str, authority: Keystore, new_owner: Identity) -> DataObject:
        # TODO: reminder that the application layer is responsible to transfer the content_key to the new owner
        result = self.put(f"{obj_id}/owner/{new_owner.id}", with_authorisation_by=authority)
        return DataObject.model_validate(result)

    def update_tags(self, obj_id: str, authority: Keystore, tags: List[DataObject.Tag]) -> DataObject:
        tags = [tag.model_dump() for tag in tags]

        result = self.put(f"{obj_id}/tags", body=tags, with_authorisation_by=authority)
        return DataObject.model_validate(result)

    def remove_tags(self, obj_id: str, authority: Keystore, keys: List[str]) -> DataObject:
        result = self.delete(f"{obj_id}/tags", body=keys, with_authorisation_by=authority)
        return DataObject.model_validate(result)
