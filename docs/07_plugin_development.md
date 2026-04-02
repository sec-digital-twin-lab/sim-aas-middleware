# Plugin Development

The DOR (Data Object Repository) and RTI (Runtime Infrastructure) are implemented as plugins, allowing custom backends without modifying core middleware code. This guide covers how to create and register custom plugins.

## Built-in Plugins

Sim-aaS ships with three built-in plugins:

| Plugin | Type | Description |
|--------|------|-------------|
| `dor_fs` | DOR | SQLite metadata + filesystem content storage. The default for local development and single-node deployments. |
| `rti_docker` | RTI | Runs processor containers on the local Docker daemon. Manages port allocation, volume mounts, and container lifecycle. |
| `rti_aws` | RTI | Runs processor containers on AWS using ECR for images and AWS Batch for execution. Supports EFS volumes and SSH tunneling. |

## Plugin Discovery

Plugins are discovered at startup from two sources:

1. **Built-in plugins** in `simaas/plugins/builtins/`, always available
2. **Additional plugin directories** specified via `--plugins PATH` on the CLI (can be repeated for multiple directories)

The discovery system scans for Python packages containing classes that subclass `DORRESTService` (for DOR plugins) or `RTIRESTService` (for RTI plugins). Each plugin is registered by the name returned from its `plugin_name()` class method.

To use a plugin when starting a node:

```bash
simaas-cli service --dor my_custom_dor --rti my_custom_rti --plugins /path/to/my/plugins
```

## Creating a DOR Plugin

A DOR plugin provides data storage and management. It must implement the `DORInterface` protocol.

### Directory Structure

```
my_dor_plugin/
  __init__.py
  service.py
```

### Implementation

In `service.py`, create a class that extends `DORRESTService`:

```python
from typing import List, Optional, Dict
from simaas.dor.api import DORRESTService
from simaas.dor.schemas import (
    DataObject, DataObjectProvenance, DORStatistics,
    SearchParameters, AddDataObjectParameters
)


class MyDORService(DORRESTService):

    @classmethod
    def plugin_name(cls) -> str:
        return 'my_dor'

    def type(self) -> str:
        return 'my_dor'

    async def search(self, parameters: SearchParameters) -> List[DataObject]:
        ...

    async def add(self, parameters: AddDataObjectParameters,
                  content_path: str) -> Optional[DataObject]:
        ...

    async def remove(self, obj_id: str) -> DataObject:
        ...

    async def get_meta(self, obj_id: str) -> Optional[DataObject]:
        ...

    async def get_content(self, obj_id: str, content_path: str) -> Optional[str]:
        ...

    async def get_provenance(self, c_hash: str) -> Optional[DataObjectProvenance]:
        ...

    async def grant_access(self, obj_id: str, user_iid: str) -> DataObject:
        ...

    async def revoke_access(self, obj_id: str, user_iid: str) -> DataObject:
        ...

    async def transfer_ownership(self, obj_id: str, new_owner_iid: str) -> DataObject:
        ...

    async def update_tags(self, obj_id: str, tags: Dict) -> DataObject:
        ...

    async def remove_tags(self, obj_id: str, keys: List[str]) -> DataObject:
        ...

    async def statistics(self) -> DORStatistics:
        ...
```

### DOR Interface Methods

| Method | Description |
|--------|-------------|
| `search(parameters)` | Search for data objects matching patterns, types, formats, owner, or content hashes. |
| `add(parameters, content_path)` | Store a new data object. `content_path` is the path to the uploaded file. |
| `remove(obj_id)` | Delete a data object and its content. |
| `get_meta(obj_id)` | Return metadata for a data object. |
| `get_content(obj_id, content_path)` | Copy the data object's content to `content_path`. Return the path on success. |
| `get_provenance(c_hash)` | Build and return the provenance graph for a content hash. |
| `grant_access(obj_id, user_iid)` | Add an identity to the access list. |
| `revoke_access(obj_id, user_iid)` | Remove an identity from the access list. |
| `transfer_ownership(obj_id, new_owner_iid)` | Change the data object's owner. |
| `update_tags(obj_id, tags)` | Add or update tags. |
| `remove_tags(obj_id, keys)` | Remove tags by key. |
| `statistics()` | Return aggregate statistics (available data types and formats). |

All methods except `type()` and `plugin_name()` are async.

## Creating an RTI Plugin

An RTI plugin handles processor deployment and job execution. It must extend `RTIServiceBase`.

### Directory Structure

```
my_rti_plugin/
  __init__.py
  service.py
```

### Implementation

```python
from typing import List, Dict, Optional
from simaas.rti.base import RTIServiceBase
from simaas.rti.schemas import Job, Processor


class MyRTIService(RTIServiceBase):

    @classmethod
    def plugin_name(cls) -> str:
        return 'my_rti'

    def type(self) -> str:
        return 'my_rti'

    async def perform_deploy(self, proc_id: str) -> None:
        # Fetch the processor image and prepare it for execution
        ...

    async def perform_undeploy(self, proc_id: str) -> None:
        # Clean up resources for the processor
        ...

    def perform_submit_single(self, job: Job, proc: Processor) -> None:
        # Start a single job container
        ...

    def perform_submit_batch(self, jobs: List[Job], proc_map: Dict[str, Processor]) -> None:
        # Start multiple job containers as a synchronized batch
        ...

    async def perform_cancel(self, job: Job) -> None:
        # Stop a running job
        ...

    async def perform_purge(self, job: Job) -> None:
        # Forcefully remove a job
        ...

    async def perform_job_cleanup(self, job: Job) -> None:
        # Clean up after a completed/failed/cancelled job
        ...

    def resolve_port_mapping(self, job: Job) -> Dict[str, str]:
        # Return the external address for each exposed port
        ...
```

### RTI Interface Methods

| Method | Description |
|--------|-------------|
| `perform_deploy(proc_id)` | Fetch the processor image and make it available for execution. The `proc_id` is a DOR object ID. |
| `perform_undeploy(proc_id)` | Remove the processor and free associated resources. |
| `perform_submit_single(job, proc)` | Start a container for a single job. Must configure the container with the custodian address, job ID, and P2P connectivity. |
| `perform_submit_batch(jobs, proc_map)` | Start containers for multiple jobs that will execute as a synchronized batch. |
| `perform_cancel(job)` | Stop a running job's container. |
| `perform_purge(job)` | Forcefully remove a job regardless of state. |
| `perform_job_cleanup(job)` | Clean up resources (containers, temp files) after a job finishes. |
| `resolve_port_mapping(job)` | Return a mapping of container ports to external addresses (e.g., `{"6000/tcp": "tcp://192.168.1.5:7234"}`). |

The base class (`RTIServiceBase`) handles job state management, database persistence, input validation, and async task coordination. Your plugin only needs to implement the container/compute-specific logic.

### Container Requirements

When starting a job container, the RTI plugin must:

1. Set environment variables: `SIMAAS_CUSTODIAN_ADDRESS` (P2P address of the node), `SIMAAS_CUSTODIAN_PUBKEY` (Curve public key), `JOB_ID`, `EXTERNAL_P2P_ADDRESS`
2. Map port 6000 (mandatory, used for P2P communication between the job runner and the custodian)
3. Map any additional exposed ports (for co-simulation)
4. Apply resource limits from the task's budget

## Plugin Registration

The `__init__.py` in your plugin directory should import the service class:

```python
from .service import MyDORService
```

The discovery system will find it automatically when the plugin directory is passed via `--plugins`.

## Security Considerations

The middleware's security model (cryptographic identity, request signing, access control) is enforced at the API layer, above the plugin level. DOR and RTI plugins receive pre-authenticated, pre-authorized requests. They do not need to implement authentication or authorization themselves.

If you need user-facing auth (OAuth2, LDAP, SSO), implement it in an application server that sits in front of the middleware and translates user sessions into middleware identity-signed requests.
