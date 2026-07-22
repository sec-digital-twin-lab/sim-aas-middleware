# Plugin Development

The DOR (Data Object Repository) and RTI (Runtime Infrastructure) services are implemented as plugins, allowing custom implementations for different storage backends or execution environments.

## Plugin Directory Structure

Built-in plugins are bundled inside the `simaas` package:

```
simaas/plugins/builtins/
  __init__.py              # Builtins package marker
  dor_fs/                  # DOR plugin using SQLite + local filesystem
    __init__.py            # Exports the service class
    service.py             # Implementation
  rti_docker/              # RTI plugin for local Docker execution
    __init__.py
    service.py
  rti_aws/                 # RTI plugin for AWS Batch execution
    __init__.py
    service.py
```

## Built-in Plugins

| Plugin | Purpose | Dependencies |
|--------|---------|--------------|
| `dor_fs` | Filesystem DOR using SQLite + local storage | Core dependencies |
| `rti_docker` | Local Docker execution | Core dependencies |
| `rti_aws` | AWS Batch execution | `boto3` (included in requirements.txt) |

## Creating a DOR Plugin

DOR plugins must implement the `DORInterface` and provide a `plugin_name()` class method:

```python
# simaas/plugins/builtins/dor_custom/__init__.py
from .service import CustomDORService
__all__ = ['CustomDORService']

# simaas/plugins/builtins/dor_custom/service.py
from simaas.dor.api import DORInterface

class CustomDORService(DORInterface):
    @classmethod
    def plugin_name(cls) -> str:
        return "custom"  # Name shown in CLI selection

    def __init__(self, node, db_path: str):
        # Initialize your storage backend
        pass

    # Required DORInterface methods (all synchronous):
    def type(self) -> str: ...
    def search(self, patterns, owner_iid, data_type, data_format, c_hashes) -> List[DataObject]: ...
    def statistics(self) -> DORStatistics: ...
    def add(self, content_path, data_type, data_format, owner_iid, ...) -> DataObject: ...
    def remove(self, obj_id) -> Optional[DataObject]: ...
    def get_meta(self, obj_id) -> Optional[DataObject]: ...
    def get_content(self, obj_id, content_path) -> None: ...
    def get_provenance(self, c_hash) -> Optional[DataObjectProvenance]: ...
    def grant_access(self, obj_id, user_iid) -> DataObject: ...
    def revoke_access(self, obj_id, user_iid) -> DataObject: ...
    def transfer_ownership(self, obj_id, new_owner_iid) -> DataObject: ...
    def update_tags(self, obj_id, tags) -> DataObject: ...
    def remove_tags(self, obj_id, keys) -> DataObject: ...
```

## Creating an RTI Plugin

RTI plugins must extend `RTIServiceBase` and provide a `plugin_name()` class method:

```python
# simaas/plugins/builtins/rti_custom/__init__.py
from .service import CustomRTIService
__all__ = ['CustomRTIService']

# simaas/plugins/builtins/rti_custom/service.py
from simaas.rti.base import RTIServiceBase

class CustomRTIService(RTIServiceBase):
    @classmethod
    def plugin_name(cls) -> str:
        return "custom"  # Name shown in CLI selection

    def __init__(self, node, db_path: str, retain_job_history: bool = False,
                 strict_deployment: bool = True):
        super().__init__(node, db_path, retain_job_history, strict_deployment)

    # Abstract methods to override (all synchronous):
    def perform_deploy(self, proc: Processor) -> None: ...
    def perform_undeploy(self, proc: Processor, keep_image: bool = True) -> None: ...
    def perform_submit_single(self, job: Job, proc: Processor) -> None: ...
    def perform_submit_batch(self, batch: List[Tuple], batch_id: str) -> None: ...
    def perform_cancel(self, job_id: str, ...) -> None: ...
    def perform_purge(self, job_record: DBJobInfo) -> None: ...
    def perform_job_cleanup(self, job_id: str) -> None: ...
    def resolve_port_mapping(self, job_id: str, runner_details: dict) -> dict: ...
```

## Plugin Discovery

Plugins are discovered automatically at startup from:
1. The built-in `simaas/plugins/builtins/` directory (bundled with the package)
2. Additional directories specified via `--plugins` CLI argument

The plugin name (returned by `plugin_name()`) is used for CLI selection. Plugin folder names should use underscores (e.g., `dor_postgres`, `rti_kubernetes`).

## Extension Patterns

### Custom P2P Protocols

```python
from typing import Optional, Tuple
from pydantic import BaseModel
from simaas.core.identity import Identity
from simaas.decorators import p2p_public_access, p2p_requires_authentication
from simaas.p2p.base import P2PProtocol

class MyRequest(BaseModel):
    payload: str

class MyResponse(BaseModel):
    result: str

# Every P2P protocol must carry exactly one auth marker.
@p2p_requires_authentication
class CustomProtocol(P2PProtocol):
    NAME = 'custom-op'

    def __init__(self, node):
        super().__init__(self.NAME)
        self._node = node

    @classmethod
    def perform(cls, peer_address, keystore, request: MyRequest) -> MyResponse:
        # Client-side entry point. Use simaas.p2p.base.p2p_request under the hood.
        ...

    def handle(self, request: MyRequest, attachment_path=None, download_path=None,
               identity: Optional[Identity] = None) -> Tuple[Optional[BaseModel], Optional[str]]:
        # `identity` is the mTLS-verified caller (guaranteed non-None because
        # this protocol carries @p2p_requires_authentication).
        return MyResponse(result=f"hello {identity.id}"), None

    @staticmethod
    def request_type(): return MyRequest

    @staticmethod
    def response_type(): return MyResponse
```

### Custom REST Endpoints

```python
from fastapi import APIRouter, Depends

def add_custom_endpoints(rest_service) -> None:
    """Add domain-specific REST endpoints"""
    router = APIRouter()

    # Plain `def` is fine — FastAPI will schedule it on a thread. Use `async def`
    # only if you need to `await request.body()` / `await request.form()`.
    @router.get("/custom/endpoint")
    def custom_endpoint(auth = Depends(authenticate)):
        return {"custom": "response"}

    rest_service.add_custom_endpoints(router)
```

### Processor Development

See [Processor Implementation](processor_implementation.md) for building custom processors and adapters.

**Extension points for processors**:
- Custom resource types via `ResourceDescriptor` extension
- Custom inter-processor communication patterns
- Custom data validators for input/output
- Rich progress reporting with custom metrics

## Security Extensions

### Custom Authentication Providers

Possible integrations:
- **LDAP**: Enterprise directory service
- **OAuth2/OIDC**: Web-based authentication
- **HSM**: Hardware Security Module integration
- **MFA**: Multi-factor authentication

### Audit and Compliance

Extension points for:
- Comprehensive audit logging
- Compliance reporting and checking
- Data governance policies
- Regulatory framework support (GDPR, HIPAA, etc.)
