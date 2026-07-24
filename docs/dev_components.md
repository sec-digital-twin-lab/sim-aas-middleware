# Component Reference

This document provides technical details for each major component in the Sim-aaS Middleware.

## Core Infrastructure (`simaas/core/`)

Provides foundational services for identity management, cryptography, and computational model integration.

### Identity Management

Self-verifying cryptographic identities with multi-key cryptography:

```python
class Identity:
    """Public identity with verification capabilities"""
    id: str           # SHA-256 hash of canonical identity representation
    profile: IdentityProfile
    s_public_key: str  # Signing key (EC) PEM
    e_public_key: str  # Encryption key (RSA) PEM
    tls_cert: str      # Self-signed X.509 TLS cert PEM (mTLS peer identity)
    signature: str

    def verify_integrity(self) -> bool:
        """Verify identity signature integrity"""
```

### Keystore Security

Hierarchical encryption with master key protection:

```python
class Keystore:
    """Secure storage for cryptographic materials and credentials"""
    master_key: RSAKeyPair                # Root of trust, encrypted with password
    signing_key: ECKeyPair                # Signs REST requests and P2P attestations
    encryption_key: RSAKeyPair            # For data encryption
    tls_cert: TLSCertAsset                # Self-signed X.509 cert + key for P2P mTLS
    content_keys: ContentKeysAsset        # Per-data-object encryption keys
    ssh_credentials: SSHCredentialsAsset  # Git repository access
    github_credentials: GithubCredentialsAsset  # GitHub API access
```

**Security Model**:
1. Master key encrypted with user password using PBKDF2
2. All other keys encrypted with master key
3. Thread-safe with mutex protection
4. Content keys derived from master key + content hash

### Processor Framework

Template method pattern with standardized lifecycle:

```python
class ProcessorBase(ABC):
    """Abstract base for all computational processors"""

    @abstractmethod
    def run(self, working_directory: str, job: Job,
            listener: ProgressListener, namespace: Namespace,
            secrets: Dict[str, str]) -> None:
        """Execute processor logic"""

    @abstractmethod
    def interrupt(self) -> None:
        """Cancel execution gracefully"""
```

---

## Data Object Repository (`simaas/dor/`)

Distributed storage system for immutable data objects with metadata, provenance tracking, and fine-grained access control.

### Data Object Model

Content-addressable storage with rich metadata:

```python
class DataObject:
    obj_id: str                    # UUID for object identification
    c_hash: str                    # SHA-256 content hash
    data_type: str                 # Semantic type (e.g., "ProcessorDockerImage")
    data_format: str               # Format specification (e.g., "json", "tar")

    # Access control
    owner_iid: str                 # Owner identity ID
    access_restricted: bool        # Whether access control applies
    access: List[str]              # List of identity IDs with access

    # Metadata and provenance
    created: CreationDetails
    tags: Dict[str, Any]
    recipe: DataObjectRecipe       # Provenance and creation details

    # Storage
    custodian: str                 # Node responsible for storage
    content_encrypted: bool
```

### Storage Architecture

- **Local Layer**: File system storage with hash-based naming, SQLAlchemy metadata, full-text search
- **Distributed Layer**: Automatic replication, P2P discovery, intelligent caching, eventual consistency

### Access Control

Owner-based permissions with explicit grants:

```python
if data_object.access_restricted:
    if requester_iid not in data_object.access:
        raise AccessDeniedError()
```

---

## Runtime Infrastructure (`simaas/rti/`)

Job execution engine for computational workloads with containerized processors and multi-platform deployment.

### Processor Lifecycle

```
[INACTIVE] → deploy() → [BUSY_DEPLOY] → [READY]
    ↑                                      ↓
    └── undeploy() ← [BUSY_UNDEPLOY] ← ──┘
```

### Job Execution Model

```python
class Task:
    proc_id: str                      # Processor to execute
    user_iid: str                     # Identity submitting task
    input: List[InputValue]           # Input data objects
    output: List[OutputSpecification] # Output requirements
    budget: ResourceDescriptor        # CPU/memory allocation
    secrets: List[str]                # Required environment secrets

class Job:
    id: str                          # Unique job identifier
    batch_id: str                    # Batch grouping
    task: Task
    custodian: NodeInfo              # Node managing execution
```

### Resource Management

```python
class ResourceDescriptor:
    vcpus: int = 1         # CPU allocation
    memory: int = 2048     # Memory in MB
    timeout: int = 3600    # Maximum execution time
```

### Execution Backends

**Docker (Local)**:
```python
container = docker_client.containers.run(
    image=processor_image,
    cpu_count=task.budget.vcpus,
    mem_limit=f"{task.budget.memory}m",
    detach=True
)
```

**AWS Batch (Cloud)**:
```python
response = batch_client.submit_job(
    jobName=f"simaas-{task.id}",
    jobQueue=self.job_queue,
    jobDefinition=self.job_definition
)
```

---

## P2P Networking (`simaas/p2p/`)

Framed request/response over TLS 1.3 with mutual authentication. The server is a
`socketserver.ThreadingTCPServer` (`_ThreadedSSLServer` in `simaas/p2p/service.py`)
that rebuilds its SSL context per accept so the trust bundle stays current with the
node's identity DB.

### Transport Model

Client dials with the peer's expected server cert pinned, and — for authenticated
protocols — also presents its own keystore's TLS cert:

```python
# simaas/p2p/base.py
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.minimum_version = ssl.TLSVersion.TLSv1_3
ctx.verify_mode = ssl.CERT_REQUIRED
ctx.load_verify_locations(cadata=peer.tls_cert)  # pin peer's self-signed cert
if with_authorisation_by is not None:
    # present the client cert so the server can identify the caller via mTLS
    ctx.load_cert_chain(certfile=..., keyfile=...)
```

Server accepts, requests a client cert, and verifies it against a bundle built from
every identity the node currently knows about (`node.db.get_identities()`):

```python
# simaas/p2p/service.py
def _current_trust_bundle(self):
    parts = [i.tls_cert for i in self._node.db.get_identities() if i.tls_cert]
    return "\n".join(parts) if parts else None
```

An empty bundle disables client-cert verification (bootstrap), so `@p2p_public_access`
protocols (identity publish, network discovery) work before any peers are known.

### Protocol Framework

```python
class P2PProtocol(ABC):
    """Base class for P2P protocols. Concrete classes carry an auth marker."""

    @classmethod
    @abstractmethod
    def perform(cls, ...):
        """Client-side entry point."""

    @abstractmethod
    def handle(self, request, attachment_path=None, download_path=None,
               identity: Optional[Identity] = None) -> Tuple[Optional[BaseModel], Optional[str]]:
        """Server-side handler. `identity` is the mTLS-verified caller (or None)."""
```

Every concrete protocol carries either `@p2p_public_access` or
`@p2p_requires_authentication`; unmarked protocols are rejected at `add()` time.

### Ownership on push

Data-object pushes attribute ownership to the mTLS-verified sender by default. A push
may carry a `RelayAttestation` naming a different attester as the rightful owner; the
target verifies the attestation signature against the attester's identity record and,
if valid, records ownership as the attester rather than the immediate sender. This is
how a runner can push through its custodian while keeping ownership on itself. See
`simaas/dor/protocol.py::P2PPushDataObject.handle` and
`P2PRelayPushDataObject.handle`.

### Built-in Protocols

| Protocol | Purpose |
|----------|---------|
| `P2PLookupDataObject` / `P2PFetchDataObject` | Discover and fetch data objects |
| `P2PPushDataObject` / `P2PRelayPushDataObject` | Upload (direct or via a relay) |
| `P2PUpdateIdentity` / `P2PGetIdentity` | Publish and query identity records |
| `P2PJoinNetwork` / `P2PLeaveNetwork` / `P2PGetNetwork` | Network membership |
| `P2PPushJobStatus` / `P2PInterruptJob` / `BatchBarrier` | RTI runner ↔ custodian |

---

## Node Database (`simaas/nodedb/`)

Network registry and discovery service for maintaining information about network participants.

### Node Information Model

```python
class NodeInfo:
    identity: Identity              # Node's cryptographic identity
    last_seen: int                  # Timestamp of last communication

    # Service capabilities
    dor_service: bool               # Provides data storage
    rti_service: bool               # Provides job execution

    # Network information
    p2p_address: str                # P2P communication endpoint
    rest_address: Tuple[str, int]   # REST API endpoint
```

### Service Discovery

```python
def find_storage_nodes(self) -> List[NodeInfo]:
    return self.nodedb.search_nodes(SearchCriteria(dor_service=True, online=True))

def find_execution_nodes(self, requirements: ResourceDescriptor) -> List[NodeInfo]:
    return self.nodedb.search_nodes(SearchCriteria(
        rti_service=True, online=True, min_resources=requirements
    ))
```

---

## REST API (`simaas/rest/`)

HTTP-based API layer for external system integration using FastAPI with cryptographic authentication.

### Authentication

Requests are authenticated using cryptographic signatures derived from the caller's keystore.
There are no sessions, cookies, or JWTs — every request is independently verifiable.

**Required Headers**:

| Header | Value |
|--------|-------|
| `saasauth-iid` | The caller's identity ID |
| `saasauth-timestamp` | Unix seconds when the signature was issued |
| `saasauth-signature` | Signature over method + URL + timestamp + body |

**Signature Generation**:

The signature covers the request target, an `issued_at` timestamp, and the body — so it
can't be replayed across endpoints, mutated payloads, or old requests. The server enforces
a time window (default 300 s; override with `SIMAAS_SIG_WINDOW_SECONDS`) around
`issued_at`.

```python
import time
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
import canonicaljson

# The transport-layer canonicalisation lives in simaas.rest.canonical
from simaas.rest.canonical import REST_AUTH_DOMAIN, canonical_auth_url

issued_at = int(time.time())

# 1. Build the digest: domain-separated hash of METHOD:URL + issued_at + canonical body
digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
digest.update(REST_AUTH_DOMAIN)
digest.update(canonical_auth_url(f"{method}:{full_url}").encode('utf-8'))
digest.update(str(issued_at).encode('utf-8'))
if body:
    digest.update(canonicaljson.encode_canonical_json(body))
token = digest.finalize()

# 2. Sign the digest with the keystore's EC signing key
signature = keystore.sign(token)

# 3. Set headers
headers = {
    'saasauth-iid': keystore.identity.id,
    'saasauth-timestamp': str(issued_at),
    'saasauth-signature': signature,
}
```

**Server-Side Verification** (`simaas/rest/auth.py::VerifyAuthorisation`):

1. Reject with 403 if any of the three headers is missing.
2. Parse `saasauth-timestamp` and check it against the current time window
   (`timestamp_within_window`); reject with 403 if it's outside.
3. Look up the identity by `saasauth-iid` in the node database.
4. Recompute the digest from `METHOD:URL` + `issued_at` + body (multipart requests use
   the JSON-decoded `body` form field; see `_extract_signed_body`).
5. Verify the signature against the identity's public signing key; reject with 403 if
   the identity is unknown or the signature is invalid.

Error responses always carry `{reason, id}` only — the full `details` dict is logged
server-side under the same `id` and never sent to the client (see
`simaas/rest/service.py::_error_response`). Include the id when reporting an issue.

**Using Proxy Classes**: The `DORProxy`, `RTIProxy`, and `NodeDBProxy` classes handle signature
generation automatically — pass a `Keystore` instance and authentication is transparent.
See `simaas/rest/proxy.py` for the implementation.

### Key Endpoints

All endpoints are prefixed with `/api/v1`.

**DOR Endpoints** (`/api/v1/dor`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/dor` | GET | Search for data objects |
| `/api/v1/dor/statistics` | GET | Get DOR statistics |
| `/api/v1/dor/add` | POST | Upload new data object |
| `/api/v1/dor/{obj_id}` | DELETE | Remove data object |
| `/api/v1/dor/{obj_id}/meta` | GET | Get object metadata |
| `/api/v1/dor/{obj_id}/content` | GET | Download object content |
| `/api/v1/dor/{c_hash}/provenance` | GET | Get provenance by content hash |
| `/api/v1/dor/{obj_id}/access/{user_iid}` | POST | Grant access to user |
| `/api/v1/dor/{obj_id}/access/{user_iid}` | DELETE | Revoke access from user |
| `/api/v1/dor/{obj_id}/owner/{new_owner_iid}` | PUT | Transfer ownership |
| `/api/v1/dor/{obj_id}/tags` | PUT | Update tags |
| `/api/v1/dor/{obj_id}/tags` | DELETE | Remove tags |

**RTI Endpoints** (`/api/v1/rti`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/rti/proc` | GET | List deployed processors |
| `/api/v1/rti/proc/{proc_id}` | GET | Get processor details |
| `/api/v1/rti/proc/{proc_id}/deploy` | POST | Deploy processor |
| `/api/v1/rti/proc/{proc_id}/undeploy` | POST | Undeploy processor |
| `/api/v1/rti/job` | GET | List jobs |
| `/api/v1/rti/job/submit` | POST | Submit job |
| `/api/v1/rti/job/{job_id}/status` | GET | Get job status |
| `/api/v1/rti/job/{job_id}/cancel` | POST | Cancel job |

**Other Endpoints**:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/db/node` | GET | List known network nodes |
| `/api/v1/docs` | GET | OpenAPI documentation |

---

## Command Line Interface (`simaas/cli/`)

Comprehensive CLI providing user-friendly access to all platform capabilities.

### Command Organization

```
simaas-cli
├── identity          # Identity and credential management
│   ├── create, remove, show, update, list
│   ├── discover, publish
│   └── credentials
│       ├── add (ssh, github)
│       ├── test (ssh, github)
│       ├── remove, list
├── service           # Start services
│   ├── node          # Start a Sim-aaS node instance
│   └── gateway       # Start a Gateway API service
├── run               # Job runner (used inside containers)
├── image             # Processor Docker Image (PDI) management
│   ├── build-local, build-github
│   └── import, export
├── dor               # Data Object Repository
│   ├── search, add, meta, download, remove
│   ├── tag, untag
│   └── access (grant, revoke, show)
├── rti               # Runtime Infrastructure
│   ├── volume (list, create, delete)
│   ├── proc (deploy, undeploy, list, show)
│   └── job (list, submit, status, inspect, logs, cancel)
├── namespace         # Namespace management
│   └── list, update, show
├── network           # Network operations
│   └── list, ping, status
├── node              # Node diagnostics
│   └── status, info
└── gateway           # Gateway administration
    ├── user (list, create, delete, enable, disable, publish)
    └── key (list, create, delete)
```

### Interactive Features

- Progressive disclosure with intelligent prompts
- Auto-discovery of keystores
- Interactive password prompts
- Rich progress indicators for long-running operations
- Support for both interactive and batch processing modes

---

## Node Lifecycle (`simaas/node/`)

The `Node` class is the central orchestrator that manages all services. All of its
public methods are **synchronous** — see [Sync Design + FastAPI Boundary](dev_async_patterns.md).

### Lifecycle methods

```python
def startup(self, p2p_address: str, rest_address: Tuple[str, int] = None,
            bind_all_address: bool = False, wait_until_ready: bool = True) -> None:
    """Start P2P and REST daemon threads, wait for services to be ready."""

def join_network(self, boot_node_address: Tuple[str, int]) -> None:
    """Join the P2P network via a boot node (REST discovery + P2P handshake)."""

def leave_network(self, blocking: bool = False) -> None:
    """Inform peers and leave the network."""

def shutdown_rti(self, timeout: int = 60) -> None:
    """Undeploy all processors and wait for workers to finish."""

def update_identity(self, name: str = None, email: str = None,
                    propagate: bool = True) -> Identity:
    """Update identity and optionally broadcast to peers."""

def shutdown(self) -> None:
    """Stop P2P and REST daemon threads."""
```

### Usage

```python
from simaas.node.default import DefaultNode
from simaas.core.keystore import Keystore
from simaas.plugins.builtins.dor_fs import FilesystemDORService
from simaas.plugins.builtins.rti_docker import DockerRTIService

keystore = Keystore.from_file("path/to/keystore.json", password="secret")

node = DefaultNode.create(
    keystore=keystore,
    storage_path="path/to/datastore",
    p2p_address="tcp://0.0.0.0:4000",
    rest_address=("0.0.0.0", 5000),
    enable_db=True,
    dor_plugin_class=FilesystemDORService,
    rti_plugin_class=DockerRTIService,
)

# Optional: join an existing network via a boot node's REST address
node.join_network(("192.168.1.100", 5000))

# ... application runs; P2P + REST are already serving on daemon threads ...

node.leave_network()
node.shutdown_rti()
node.shutdown()
```

The P2P and REST services run in their own daemon threads (`_ThreadedSSLServer` and
uvicorn respectively). Handlers on those threads call the same sync service methods
directly — no event loops to coordinate across the boundary.
