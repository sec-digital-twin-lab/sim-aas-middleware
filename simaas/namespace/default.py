import asyncio
from typing import List, Optional, Dict, Union

from simaas.core.keystore import Keystore
from simaas.core.identity import Identity
from simaas.core.helpers import generate_random_string
from simaas.dor.api import DORInterface
from simaas.dor.schemas import DataObject, DataObjectProvenance, DORStatistics, DataObjectRecipe
from simaas.namespace.api import Namespace
from simaas.namespace.protocol import P2PNamespaceServiceCall
from simaas.p2p.base import P2PAddress
from simaas.rti.api import RTIInterface
from simaas.rti.schemas import JobStatus, Task, Job, Processor, BatchStatus


class NamespaceDOR(DORInterface):
    def __init__(self, custodian_identity: Identity, custodian_address: str, authority: Keystore):
        self._peer_address = P2PAddress(
            address=custodian_address,
            curve_secret_key=authority.curve_secret_key(),
            curve_public_key=authority.curve_public_key(),
            curve_server_key=custodian_identity.c_public_key
        )
        self._authority = authority

    def type(self) -> str:
        return 'namespace-dor'

    def search(
            self, patterns: Optional[List[str]] = None, owner_iid: Optional[str] = None,
            data_type: Optional[str] = None, data_format: Optional[str] = None, c_hashes: Optional[List[str]] = None
    ) -> List[DataObject]:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'search', args={
                'patterns': patterns,
                'owner_iid': owner_iid,
                'data_type': data_type,
                'data_format': data_format,
                'c_hashes': c_hashes
            }
        ))
        reply: List[DataObject] = [DataObject.model_validate(item) for item in reply]
        return reply

    def statistics(self) -> DORStatistics:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'statistics'
        ))
        reply: DORStatistics = DORStatistics.model_validate(reply)
        return reply

    def add(self, content_path: str, data_type: str, data_format: str, owner_iid: str,
            creators_iid: Optional[List[str]] = None, access_restricted: Optional[bool] = False,
            content_encrypted: Optional[bool] = False, license: Optional[DataObject.License] = None,
            tags: Optional[Dict[str, Union[str, int, float, bool, List, Dict]]] = None,
            recipe: Optional[DataObjectRecipe] = None) -> DataObject:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'add', args={
                'content_path': '###ATTACHMENT###',
                'data_type': data_type,
                'data_format': data_format,
                'owner_iid': owner_iid,
                'creators_iid': creators_iid,
                'access_restricted': access_restricted,
                'content_encrypted': content_encrypted,
                'license': license.model_dump() if license else None,
                'tags': tags,
                'recipe': recipe.model_dump() if recipe else None,
            }, attachment_path=content_path
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply

    def remove(self, obj_id: str) -> Optional[DataObject]:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'remove', args={
                'obj_id': obj_id
            }
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply

    def get_meta(self, obj_id: str) -> Optional[DataObject]:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'get_meta', args={
                'obj_id': obj_id
            }
        ))
        reply: DataObject = DataObject.model_validate(reply) if reply else None
        return reply

    def get_content(self, obj_id: str, content_path: str) -> None:
        asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'get_content', args={
                'obj_id': obj_id,
                'content_path': '###REPLY_ATTACHMENT###'
            }, download_path=content_path
        ))


    def get_provenance(self, c_hash: str) -> Optional[DataObjectProvenance]:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'get_provenance', args={
                'c_hash': c_hash
            }
        ))
        reply: DataObjectProvenance = DataObjectProvenance.model_validate(reply) if reply else None
        return reply

    def grant_access(self, obj_id: str, user_iid: str) -> DataObject:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'grant_access', args={
                'obj_id': obj_id,
                'user_iid': user_iid
            }
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply

    def revoke_access(self, obj_id: str, user_iid: str) -> DataObject:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'revoke_access', args={
                'obj_id': obj_id,
                'user_iid': user_iid
            }
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply

    def transfer_ownership(self, obj_id: str, new_owner_iid: str) -> DataObject:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'transfer_ownership', args={
                'obj_id': obj_id,
                'new_owner_iid': new_owner_iid
            }
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply

    def update_tags(self, obj_id: str, tags: List[DataObject.Tag]) -> DataObject:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'update_tags', args={
                'obj_id': obj_id,
                'tags': [tag.model_dump() for tag in tags]
            }
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply

    def remove_tags(self, obj_id: str, keys: List[str]) -> DataObject:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'dor', 'remove_tags', args={
                'obj_id': obj_id,
                'keys': keys
            }
        ))
        reply: DataObject = DataObject.model_validate(reply)
        return reply


class NamespaceRTI(RTIInterface):
    def __init__(self, custodian_identity: Identity, custodian_address: str, authority: Keystore):
        self._peer_address = P2PAddress(
            address=custodian_address,
            curve_secret_key=authority.curve_secret_key(),
            curve_public_key=authority.curve_public_key(),
            curve_server_key=custodian_identity.c_public_key
        )
        self._authority = authority

    def type(self) -> str:
        return 'namespace-rti'

    def get_all_procs(self) -> List[Processor]:
        reply: List[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'get_all_procs', args={}
        ))
        reply: List[Processor] = [Processor.model_validate(item) for item in reply]
        return reply

    def get_proc(self, proc_id: str) -> Optional[Processor]:
        reply: Optional[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'get_proc', args={
                'proc_id': proc_id
            }
        ))
        reply: Optional[Processor] = Processor.model_validate(reply) if reply else None
        return reply

    def submit(self, tasks: List[Task]) -> List[Job]:
        reply: List[dict] = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'submit', args={
                'tasks': [task.model_dump() for task in tasks]
            }
        ))
        reply: List[Job] = [Job.model_validate(job) for job in reply]
        return reply

    def get_job_status(self, job_id: str) -> JobStatus:
        reply: dict = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'get_job_status', args={
                'job_id': job_id
            }
        ))
        reply: JobStatus = JobStatus.model_validate(reply)
        return reply

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        reply: dict = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'get_batch_status', args={
                'batch_id': batch_id
            }
        ))
        reply: BatchStatus = BatchStatus.model_validate(reply)
        return reply

    def job_cancel(self, job_id: str) -> JobStatus:
        reply: dict = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'job_cancel', args={
                'job_id': job_id
            }
        ))
        reply: JobStatus = JobStatus.model_validate(reply)
        return reply

    def job_purge(self, job_id: str) -> JobStatus:
        reply: dict = asyncio.run(P2PNamespaceServiceCall.perform(
            self._peer_address, self._authority, 'rti', 'job_purge', args={
                'job_id': job_id
            }
        ))
        reply: JobStatus = JobStatus.model_validate(reply)
        return reply


class DefaultNamespace(Namespace):
    def __init__(
            self, name: Optional[str], custodian_identity: Identity, custodian_address: str, authority: Keystore
    ) -> None:
        super().__init__(
            NamespaceDOR(custodian_identity, custodian_address, authority),
            NamespaceRTI(custodian_identity, custodian_address, authority)
        )
        self._id = generate_random_string(16)
        self._custodian_address = custodian_address
        self._name = name
        self._authority = authority

    def id(self) -> str:
        return self._id

    def custodian_address(self) -> P2PAddress:
        return self._custodian_address

    def name(self) -> Optional[str]:
        return self._name

    def keystore(self) -> Keystore:
        return self._authority

    def destroy(self) -> None:
        pass
