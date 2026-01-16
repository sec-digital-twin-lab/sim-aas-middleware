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
    public_keys: Dict[str, PublicKey]  # Signing (EC), Encryption (RSA), Communication (Curve25519)
    signature: str

    def verify(self) -> bool:
        """Verify identity signature integrity"""
```

### Keystore Security

Hierarchical encryption with master key protection:

```python
class Keystore:
    """Secure storage for cryptographic materials and credentials"""
    master_key: RSAKeyPair      # Root of trust, encrypted with password
    signing_key: ECKeyPair      # For identity verification
    encryption_key: RSAKeyPair  # For data encryption
    communication_key: bytes    # ZeroMQ Curve key
    content_keys: Dict[str, bytes]        # Per-data-object encryption
    ssh_credentials: Dict[str, SSHKey]    # Git repository access
    github_credentials: Dict[str, Token]  # GitHub API access
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

Secure, authenticated communication layer using ZeroMQ with Curve encryption.

### Security Model

Curve25519 encryption with identity verification:

```python
socket.curve_secretkey = keystore.communication_key.secret
socket.curve_publickey = keystore.communication_key.public
socket.curve_server = True  # For servers
socket.curve_serverkey = remote_public_key  # For clients
```

### Protocol Framework

```python
class P2PProtocol(ABC):
    """Base class for P2P protocols"""

    @abstractmethod
    def handle_request(self, sender_iid: str, request: any) -> any:
        """Process incoming request and return response"""
```

### Built-in Protocols

| Protocol | Purpose |
|----------|---------|
| `NetworkDiscoveryProtocol` | Discover available nodes and services |
| `DORSearchProtocol` | Search for data objects across the network |
| `DORFetchProtocol` | Fetch object content from remote nodes |
| `IdentityPublishProtocol` | Publish identity updates |
| `IdentityDiscoveryProtocol` | Discover identities by ID or pattern |

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

Signature-based authentication:

```python
# Request headers
X-Identity-ID: <identity_id>
X-Timestamp: <unix_timestamp>
X-Signature: <signature_of_request_data>
```

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
├── service           # Start a Sim-aaS node instance
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
│   └── job (list, submit, status, cancel)
├── namespace         # Namespace management
│   └── list, update, show
└── network           # Network operations
    └── list
```

### Interactive Features

- Progressive disclosure with intelligent prompts
- Auto-discovery of keystores
- Interactive password prompts
- Rich progress indicators for long-running operations
- Support for both interactive and batch processing modes
