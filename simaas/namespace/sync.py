"""Synchronous wrappers for Namespace, for use in processor threads."""

import asyncio
import threading
from typing import List, Optional, Dict

from simaas.dor.api import DORInterface

# Thread-local storage for event loops
_thread_local = threading.local()


def _get_event_loop():
    """Get or create a reusable event loop for this thread."""
    if not hasattr(_thread_local, 'loop') or _thread_local.loop.is_closed():
        _thread_local.loop = asyncio.new_event_loop()
    return _thread_local.loop


def _run_async(coro):
    """Run coroutine in thread-local event loop.

    WARNING: Cannot be called from within an async context (running event loop).
    Doing so raises RuntimeError. Use `await` directly in async code.

    Intended for processor threads that need sync access to async APIs.
    """
    return _get_event_loop().run_until_complete(coro)


from simaas.dor.schemas import DataObject, DataObjectProvenance, DORStatistics, DataObjectRecipe, TagValueType  # noqa: E402
from simaas.namespace.api import Namespace  # noqa: E402
from simaas.rti.api import RTIInterface  # noqa: E402
from simaas.rti.schemas import JobStatus, Task, Job, Processor, BatchStatus  # noqa: E402


class SyncNamespaceDOR:
    """Sync wrapper for DOR interface, for use by processors running in threads."""

    def __init__(self, async_dor: DORInterface):
        self._async = async_dor

    def type(self) -> str:
        return self._async.type()

    def search(
            self, patterns: Optional[List[str]] = None, owner_iid: Optional[str] = None,
            data_type: Optional[str] = None, data_format: Optional[str] = None, c_hashes: Optional[List[str]] = None
    ) -> List[DataObject]:
        return _run_async(self._async.search(patterns, owner_iid, data_type, data_format, c_hashes))

    def statistics(self) -> DORStatistics:
        return _run_async(self._async.statistics())

    def add(self, content_path: str, data_type: str, data_format: str, owner_iid: str,
            creators_iid: Optional[List[str]] = None, access_restricted: Optional[bool] = False,
            content_encrypted: Optional[bool] = False, license: Optional[DataObject.License] = None,
            tags: Optional[Dict[str, TagValueType]] = None,
            recipe: Optional[DataObjectRecipe] = None) -> DataObject:
        return _run_async(self._async.add(
            content_path, data_type, data_format, owner_iid, creators_iid,
            access_restricted, content_encrypted, license, tags, recipe
        ))

    def remove(self, obj_id: str) -> Optional[DataObject]:
        return _run_async(self._async.remove(obj_id))

    def get_meta(self, obj_id: str) -> Optional[DataObject]:
        return _run_async(self._async.get_meta(obj_id))

    def get_content(self, obj_id: str, content_path: str) -> None:
        return _run_async(self._async.get_content(obj_id, content_path))

    def get_provenance(self, c_hash: str) -> Optional[DataObjectProvenance]:
        return _run_async(self._async.get_provenance(c_hash))

    def grant_access(self, obj_id: str, user_iid: str) -> DataObject:
        return _run_async(self._async.grant_access(obj_id, user_iid))

    def revoke_access(self, obj_id: str, user_iid: str) -> DataObject:
        return _run_async(self._async.revoke_access(obj_id, user_iid))

    def transfer_ownership(self, obj_id: str, new_owner_iid: str) -> DataObject:
        return _run_async(self._async.transfer_ownership(obj_id, new_owner_iid))

    def update_tags(self, obj_id: str, tags: List[DataObject.Tag]) -> DataObject:
        return _run_async(self._async.update_tags(obj_id, tags))

    def remove_tags(self, obj_id: str, keys: List[str]) -> DataObject:
        return _run_async(self._async.remove_tags(obj_id, keys))


class SyncNamespaceRTI:
    """Sync wrapper for RTI interface, for use by processors running in threads."""

    def __init__(self, async_rti: RTIInterface):
        self._async = async_rti

    def type(self) -> str:
        return self._async.type()

    def get_all_procs(self) -> List[Processor]:
        return _run_async(self._async.get_all_procs())

    def get_proc(self, proc_id: str) -> Optional[Processor]:
        return _run_async(self._async.get_proc(proc_id))

    def submit(self, tasks: List[Task]) -> List[Job]:
        return _run_async(self._async.submit(tasks))

    def get_job_status(self, job_id: str) -> JobStatus:
        return _run_async(self._async.get_job_status(job_id))

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        return _run_async(self._async.get_batch_status(batch_id))

    def job_cancel(self, job_id: str) -> JobStatus:
        return _run_async(self._async.job_cancel(job_id))

    def job_purge(self, job_id: str) -> JobStatus:
        return _run_async(self._async.job_purge(job_id))


class SyncNamespace:
    """Sync wrapper for Namespace, for use in processor threads."""

    def __init__(self, namespace: Namespace):
        self._namespace = namespace
        self._dor = SyncNamespaceDOR(namespace.dor)
        self._rti = SyncNamespaceRTI(namespace.rti)

    def id(self) -> str:
        return self._namespace.id()

    def custodian_address(self) -> str:
        return self._namespace.custodian_address()

    def name(self) -> Optional[str]:
        return self._namespace.name()

    def keystore(self):
        return self._namespace.keystore()

    def destroy(self) -> None:
        return self._namespace.destroy()

    @property
    def dor(self) -> SyncNamespaceDOR:
        return self._dor

    @property
    def rti(self) -> SyncNamespaceRTI:
        return self._rti
