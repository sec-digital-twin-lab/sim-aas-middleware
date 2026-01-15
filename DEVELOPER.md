# Sim-aaS Middleware Developer Documentation

## 1. High-Level Description

### What is Sim-aaS Middleware?

The **Simulation-as-a-Service (Sim-aaS) Middleware** is a distributed computational platform designed to enable secure, scalable execution of simulation models and computational workloads across a network of heterogeneous nodes. It provides a standardized framework for integrating, deploying, and orchestrating computational models while maintaining strong security, data provenance, and resource management capabilities.

### Primary Use Cases

1. **Distributed Scientific Computing**: Execute complex simulations across multiple nodes with automatic load balancing and resource management
2. **Federated Simulation**: Coordinate real-time co-simulations between different computational models
3. **Computational Model Integration**: Standardize interfaces for legacy and new computational models through containerized adapters
4. **Secure Data Processing**: Process sensitive data with cryptographic access controls and end-to-end encryption
5. **Multi-Cloud Orchestration**: Execute workloads across local infrastructure and cloud platforms (AWS, with extensibility for others)

### Design Philosophy and Principles

#### Security-First Architecture
- **Cryptographic Identity**: All operations are tied to verifiable cryptographic identities using EC/RSA key pairs
- **Zero-Trust Networking**: Every communication is authenticated and optionally encrypted using ZeroMQ Curve protocol
- **End-to-End Data Protection**: Data objects can be encrypted at rest and in transit with user-controlled keys
- **Fine-Grained Access Control**: Data access is controlled at the individual object level with explicit permission grants

#### Decentralized by Design
- **No Central Authority**: The system operates as a peer-to-peer network without single points of failure
- **Distributed Storage**: Data objects are replicated across network nodes for availability and resilience
- **Dynamic Discovery**: Nodes discover each other through a distributed boot node mechanism
- **Autonomous Operation**: Nodes can operate independently and rejoin the network seamlessly

#### Container-First Execution Model
- **Isolation**: All computational workloads execute in Docker containers for security and reproducibility
- **Standardization**: Processors follow a common interface contract enabling automated orchestration
- **Portability**: Support for local Docker execution and cloud platforms with minimal configuration changes
- **Resource Management**: Container-based resource allocation with CPU and memory budgeting

#### Self-Describing Components
- **Interface Contracts**: Processors define formal input/output specifications in `descriptor.json` files
- **Data Type System**: Rich metadata system for semantic data compatibility checking
- **Provenance Tracking**: Complete lineage tracking for all computational results and data transformations
- **Version Control Integration**: Built-in support for Git-based processor versioning and reproducibility

#### Scalability and Performance
- **Horizontal Scaling**: Linear performance improvement by adding more nodes to the network
- **Service Decomposition**: Independent scaling of storage (DOR) and execution (RTI) services
- **Asynchronous Operations**: Non-blocking communication patterns for high-throughput scenarios
- **Resource Optimization**: Intelligent job scheduling and data locality optimization

## 2. System Architecture

### Architectural Overview

The Sim-aaS Middleware follows a **service-oriented, microservices architecture** where each node in the network hosts a collection of specialized services that communicate through well-defined interfaces. The system is designed as a **layered architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI Layer                               │
│  (User Interface, Command Processing, Interactive Tools)    │
├─────────────────────────────────────────────────────────────┤
│                   Service Layer                             │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │   DOR   │ │   RTI   │ │  NodeDB │ │  REST   │           │
│  │(Storage)│ │(Execute)│ │(Network)│ │  (API)  │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
├─────────────────────────────────────────────────────────────┤
│                 Communication Layer                         │
│  ┌─────────────────────┐ ┌─────────────────────────────────┐ │
│  │     P2P Network     │ │      HTTP/REST API              │ │
│  │  (ZeroMQ + Curve)   │ │    (FastAPI + Auth)             │ │
│  └─────────────────────┘ └─────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                    Core Layer                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌─────────────────┐ │
│  │Identity │ │Keystore │ │ Processor   │ │   Namespaces    │ │
│  │Management│ │Security │ │ Framework   │ │ & Resources     │ │
│  └─────────┘ └─────────┘ └─────────────┘ └─────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Network Topology

The system operates as a **structured peer-to-peer network** where:
- **Boot Nodes**: Special nodes that facilitate network joining and initial peer discovery
- **Full Nodes**: Provide both storage (DOR) and execution (RTI) services
- **Specialized Nodes**: Can run storage-only or execution-only configurations
- **Client Nodes**: Lightweight nodes that consume services without hosting them

### Data Flow Architecture

```
User Input → CLI → REST API → Service Layer → P2P Network → Remote Nodes
     ↓                                      ↓
Data Objects ← DOR ← Database ← Processing Results ← RTI ← Processors
```

## 3. Major Components and System Design

### 3.1 Core Infrastructure (`simaas/core/`)

#### Purpose
Provides foundational services for identity management, cryptography, and computational model integration.

#### Key Components

##### Identity Management System
**Architecture**: Self-verifying cryptographic identities with multi-key cryptography

```python
class Identity:
    """Public identity with verification capabilities"""
    def __init__(self, id: str, profile: IdentityProfile, 
                 public_keys: Dict[str, PublicKey], signature: str)
    
    def verify(self) -> bool:
        """Verify identity signature integrity"""
```

**Design Details**:
- **Identity ID**: SHA-256 hash of canonical identity representation
- **Multi-Key Support**: Separate keys for signing (EC), encryption (RSA), and communication (Curve25519)
- **Self-Verification**: Identities include signatures that prove key ownership
- **Immutable**: Identity changes require new identity creation

##### Keystore Security Architecture
**Architecture**: Hierarchical encryption with master key protection

```python
class Keystore:
    """Secure storage for cryptographic materials and credentials"""
    def __init__(self, keystore_path: str, password: str)
    
    # Core assets
    master_key: RSAKeyPair      # Root of trust, encrypted with password
    signing_key: ECKeyPair      # For identity verification  
    encryption_key: RSAKeyPair  # For data encryption
    communication_key: bytes    # ZeroMQ Curve key
    
    # Extended assets
    content_keys: Dict[str, bytes]        # Per-data-object encryption
    ssh_credentials: Dict[str, SSHKey]    # Git repository access
    github_credentials: Dict[str, Token]  # GitHub API access
```

**Security Model**:
1. **Master Key**: RSA-4096 key encrypted with user password using PBKDF2
2. **Asset Encryption**: All other keys encrypted with master key
3. **Thread Safety**: Mutex-protected for concurrent access
4. **Key Derivation**: Content keys derived from master key + content hash
5. **Secure Storage**: JSON format with encrypted binary blobs

##### Processor Framework
**Architecture**: Template method pattern with standardized lifecycle

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

**Design Patterns**:
- **Template Method**: Standardized execution lifecycle
- **Observer Pattern**: Progress and output notifications via `ProgressListener`
- **Dependency Injection**: Namespace provides access to platform services
- **Command Pattern**: Processors as executable commands with parameters

#### Developer Considerations

1. **Thread Safety**: All core components are thread-safe for concurrent access
2. **Error Handling**: Comprehensive exception hierarchy with detailed context
3. **Logging**: Structured logging with configurable levels and destinations
4. **Configuration**: Environment-based configuration with secure defaults
5. **Extensibility**: Plugin architecture for custom key types and processors

### 3.2 Data Object Repository (DOR) (`simaas/dor/`)

#### Purpose
Distributed storage system for immutable data objects with metadata, provenance tracking, and fine-grained access control.

#### Architecture Overview
**Pattern**: Repository pattern with distributed caching and P2P synchronization

```python
class DORInterface(ABC):
    """Abstract interface for data object operations"""
    
    @abstractmethod
    def add(self, content_path: str, data_type: str, 
            data_format: str, owner_iid: str) -> DataObject
    
    @abstractmethod
    def search(self, patterns: List[str] = None, 
               owner_iid: str = None, data_type: str = None) -> List[DataObject]
    
    @abstractmethod
    def get_content(self, obj_id: str, content_path: str) -> None
```

#### Data Object Model
**Architecture**: Content-addressable storage with rich metadata

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
    created: CreationDetails       # Timestamp and creator information
    tags: Dict[str, Any]           # Flexible metadata system
    recipe: DataObjectRecipe       # Provenance and creation details
    license: License               # Usage licensing information
    
    # Storage and security
    custodian: str                 # Node responsible for storage
    content_encrypted: bool        # Whether content is encrypted
    last_accessed: int             # Access tracking
```

#### Storage Architecture

##### Local Storage Layer
- **File System**: Content stored as files with hash-based naming
- **Database**: SQLAlchemy-based metadata storage with JSON columns
- **Indexing**: Full-text search capabilities for metadata and tags
- **Cleanup**: Automatic garbage collection for unreferenced objects

##### Distributed Storage Layer
- **Replication**: Automatic content replication across network nodes
- **Discovery**: P2P protocols for locating objects across the network
- **Caching**: Intelligent caching based on access patterns and proximity
- **Consistency**: Eventually consistent with conflict resolution

#### Access Control System
**Architecture**: Owner-based permissions with explicit grants

```python
# Access control workflow
if data_object.access_restricted:
    if requester_iid not in data_object.access:
        raise AccessDeniedError()
        
# Grant access
data_object.access.append(new_user_iid)
dor.update_access(obj_id, data_object.access)
```

#### P2P Integration
**Protocols**: Custom ZeroMQ protocols for distributed operations

```python
class DORSearchProtocol(P2PProtocol):
    """Search for data objects across network"""
    
class DORFetchProtocol(P2PProtocol):
    """Fetch object content from remote nodes"""
    
class DORPushProtocol(P2PProtocol):
    """Push objects to specific nodes"""
```

#### Developer Considerations

1. **Content Immutability**: Objects are immutable once created; modifications create new objects
2. **Hash Verification**: Always verify content hash after retrieval
3. **Large File Handling**: Streaming support for large data objects
4. **Batch Operations**: Support for bulk operations to improve performance
5. **Metadata Indexing**: Rich query capabilities through metadata and tags

### 3.3 Runtime Infrastructure (RTI) (`simaas/rti/`)

#### Purpose
Job execution engine for computational workloads with support for containerized processors, resource management, and multi-platform deployment.

#### Architecture Overview
**Pattern**: Command pattern with state machines for lifecycle management

```python
class RTIInterface(ABC):
    """Abstract interface for job execution"""
    
    @abstractmethod
    def deploy(self, proc_id: str, keystore: Keystore) -> None
        """Deploy processor for execution"""
    
    @abstractmethod
    def submit(self, tasks: List[Task]) -> List[Job]
        """Submit computational tasks"""
    
    @abstractmethod
    def get_job_status(self, job_id: str) -> JobStatus
        """Monitor job execution status"""
```

#### Processor Lifecycle Management
**State Machine**: Processor deployment and management

```
[INACTIVE] → deploy() → [BUSY_DEPLOY] → [READY]
    ↑                                      ↓
    └── undeploy() ← [BUSY_UNDEPLOY] ← ──┘
```

#### Job Execution Model
**Architecture**: Task-based execution with resource budgets

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
    batch_id: str                    # Batch grouping (for related jobs)
    task: Task                       # Task specification
    custodian: NodeInfo             # Node managing execution
    proc_name: str                  # Processor name for execution
    t_submitted: int                # Submission timestamp
```

#### Resource Management
**Architecture**: Container-based resource allocation

```python
class ResourceDescriptor:
    vcpus: int = 1                   # CPU allocation
    memory: int = 2048               # Memory in MB
    timeout: int = 3600              # Maximum execution time
    
class ResourceManager:
    def allocate_resources(self, task: Task) -> Container:
        """Allocate container resources for task execution"""
    
    def monitor_usage(self, job_id: str) -> ResourceUsage:
        """Monitor resource consumption during execution"""
```

#### Multi-Platform Execution

##### Docker Backend (Local Execution)
```python
class DockerRTI(RTIInterface):
    """Local Docker-based execution"""
    
    def _create_container(self, proc_id: str, task: Task) -> str:
        container = self.docker_client.containers.run(
            image=processor_image,
            command=["simaas-cli", "run"],
            environment=secrets,
            cpu_count=task.budget.vcpus,
            mem_limit=f"{task.budget.memory}m",
            detach=True
        )
        return container.id
```

##### AWS Batch Backend (Cloud Execution)
```python
class AWSBatchRTI(RTIInterface):
    """AWS Batch-based cloud execution"""
    
    def submit_batch_job(self, task: Task) -> str:
        response = self.batch_client.submit_job(
            jobName=f"simaas-{task.id}",
            jobQueue=self.job_queue,
            jobDefinition=self.job_definition,
            parameters=self._prepare_parameters(task)
        )
        return response['jobId']
```

#### Job Monitoring and Progress Tracking
**Architecture**: Observer pattern with real-time updates

```python
class JobStatus:
    state: State                     # Current execution state
    progress: float                  # Completion percentage (0-100)
    output: Dict[str, DataObject]    # Generated outputs
    errors: List[str]                # Error messages
    message: str                     # Current status message

class ProgressListener(ABC):
    @abstractmethod
    def on_progress_update(self, progress: float) -> None
    
    @abstractmethod
    def on_output_available(self, output_name: str) -> None
    
    @abstractmethod
    def on_message(self, severity: Severity, message: str) -> None
```

#### Batch Processing and Coordination
**Architecture**: Batch coordination for multi-processor scenarios

```python
class BatchStatus:
    batch_id: str                    # Batch identifier
    state: State                     # Overall batch state
    members: List[Member]            # Individual job information
    
    class Member:
        name: str                    # Job name within batch
        job_id: str                  # Individual job ID
        ports: Dict[str, str]        # Exposed network ports
        custodian_address: str       # Node managing this job
```

#### Developer Considerations

1. **Resource Limits**: Always specify resource budgets to prevent resource exhaustion
2. **Cancellation Support**: Implement proper interrupt handling in processors
3. **Progress Reporting**: Regular progress updates improve user experience
4. **Error Handling**: Structured error reporting with detailed context
5. **State Persistence**: Job state survives node restarts when `retain_job_history=True`

### 3.4 Peer-to-Peer Networking (`simaas/p2p/`)

#### Purpose
Secure, authenticated communication layer for direct node-to-node interactions using ZeroMQ with Curve encryption.

#### Architecture Overview
**Pattern**: Protocol-based message handling with async operations

```python
class P2PService:
    """ZeroMQ-based P2P networking service"""
    
    def __init__(self, keystore: Keystore, protocols: Dict[str, P2PProtocol])
    def startup(self, address: str) -> None
    def send_request(self, target_address: str, protocol: str, 
                    request: any) -> any
```

#### Security Model
**Architecture**: Curve25519 encryption with identity verification

```python
# Connection establishment
curve_public_key = keystore.communication_key.public
curve_secret_key = keystore.communication_key.secret

socket.curve_secretkey = curve_secret_key
socket.curve_publickey = curve_public_key
socket.curve_server = True  # For servers
socket.curve_serverkey = remote_public_key  # For clients
```

#### Protocol Framework
**Architecture**: Extensible protocol system for different operations

```python
class P2PProtocol(ABC):
    """Base class for P2P protocols"""
    
    @abstractmethod
    def handle_request(self, sender_iid: str, request: any) -> any:
        """Process incoming request and return response"""
    
    @abstractmethod
    def get_request_schema(self) -> dict:
        """JSON schema for request validation"""
    
    @abstractmethod  
    def get_response_schema(self) -> dict:
        """JSON schema for response validation"""
```

#### Built-in Protocols

##### Network Discovery
```python
class NetworkDiscoveryProtocol(P2PProtocol):
    """Discover available nodes and services"""
    
    def handle_request(self, sender_iid: str, request: dict) -> dict:
        return {
            "node_info": self.node.get_info(),
            "services": self.node.get_available_services(),
            "network_peers": self.node.get_known_peers()
        }
```

##### Data Object Operations
```python
class DORSearchProtocol(P2PProtocol):
    """Search for data objects across the network"""
    
class DORFetchProtocol(P2PProtocol):
    """Fetch object content from remote nodes"""
    
class DORPushProtocol(P2PProtocol):
    """Push objects to specific nodes for replication"""
```

##### Identity Management
```python
class IdentityPublishProtocol(P2PProtocol):
    """Publish identity updates across network"""
    
class IdentityDiscoveryProtocol(P2PProtocol):
    """Discover identities by ID or pattern"""
```

#### Asynchronous Operations
**Architecture**: Non-blocking communication with futures

```python
async def send_request_async(self, target_address: str, 
                           protocol: str, request: any) -> any:
    """Send request asynchronously and return future"""
    
    future = asyncio.Future()
    self._pending_requests[request_id] = future
    
    # Send request via ZeroMQ
    await self._send_message(target_address, message)
    
    return await future
```

#### Error Handling and Reliability
**Features**: Robust error handling with retry logic

```python
class P2PException(Exception):
    """Base exception for P2P operations"""
    
class P2PTimeoutError(P2PException):
    """Request timeout exception"""
    
class P2PConnectionError(P2PException):
    """Connection establishment failure"""
    
class P2PProtocolError(P2PException):
    """Protocol-level error (invalid request/response)"""
```

#### Developer Considerations

1. **Protocol Design**: Keep request/response schemas simple and versioned
2. **Timeout Handling**: Always specify appropriate timeouts for operations
3. **Authentication**: All protocols receive verified sender identity
4. **Schema Validation**: Use JSON schemas for request/response validation
5. **Error Propagation**: Proper exception handling across protocol boundaries

### 3.5 Node Database (`simaas/nodedb/`)

#### Purpose
Network registry and discovery service for maintaining information about network participants, their capabilities, and resource availability.

#### Architecture Overview
**Pattern**: Registry pattern with distributed updates

```python
class NodeDBInterface(ABC):
    """Abstract interface for node registry operations"""
    
    @abstractmethod
    def register_node(self, node_info: NodeInfo) -> None
        """Register node in the network"""
    
    @abstractmethod
    def search_nodes(self, criteria: SearchCriteria) -> List[NodeInfo]
        """Find nodes matching criteria"""
    
    @abstractmethod
    def update_node_status(self, node_id: str, status: NodeStatus) -> None
        """Update node availability status"""
```

#### Node Information Model
**Architecture**: Comprehensive node metadata with capabilities

```python
class NodeInfo:
    identity: Identity              # Node's cryptographic identity
    last_seen: int                 # Timestamp of last communication
    
    # Service capabilities
    dor_service: bool              # Provides data storage
    rti_service: bool              # Provides job execution
    
    # Network information
    p2p_address: str               # P2P communication endpoint
    rest_address: Tuple[str, int]  # REST API endpoint (if available)
    
    # Configuration
    retain_job_history: bool       # Whether node keeps job history
    strict_deployment: bool        # Whether node enforces strict deployment
```

#### Network Discovery Mechanisms

##### Bootstrap Discovery
```python
def join_network(self, boot_node_address: str) -> None:
    """Join network via bootstrap node"""
    
    # Connect to boot node
    boot_response = self.p2p.send_request(
        boot_node_address, 
        "network_discovery", 
        {"action": "join"}
    )
    
    # Update local node database
    for node_info in boot_response["known_nodes"]:
        self.nodedb.register_node(NodeInfo.from_dict(node_info))
```

##### Periodic Updates
```python
def update_network_status(self) -> None:
    """Periodically update network status"""
    
    for node in self.nodedb.get_all_nodes():
        try:
            status = self.p2p.send_request(
                node.p2p_address,
                "status_check",
                {"timestamp": get_timestamp_now()}
            )
            self.nodedb.update_node_status(node.identity.id, status)
        except P2PTimeoutError:
            self.nodedb.mark_node_offline(node.identity.id)
```

#### Service Discovery
**Architecture**: Capability-based service location

```python
def find_storage_nodes(self) -> List[NodeInfo]:
    """Find nodes providing DOR services"""
    return self.nodedb.search_nodes(
        SearchCriteria(dor_service=True, online=True)
    )

def find_execution_nodes(self, resource_requirements: ResourceDescriptor) -> List[NodeInfo]:
    """Find nodes capable of executing jobs"""
    return self.nodedb.search_nodes(
        SearchCriteria(
            rti_service=True,
            online=True,
            min_resources=resource_requirements
        )
    )
```

#### Identity Directory
**Architecture**: Distributed identity registry

```python
class IdentityRegistry:
    """Registry for network identities"""
    
    def publish_identity(self, identity: Identity) -> None:
        """Publish identity to network"""
        
    def discover_identity(self, identity_id: str) -> Optional[Identity]:
        """Find identity by ID"""
        
    def search_identities(self, pattern: str) -> List[Identity]:
        """Search identities by name pattern"""
```

#### Resource Management Integration
**Architecture**: Resource tracking and reservation

```python
class ResourceTracker:
    """Track resource usage across network"""
    
    def get_node_capacity(self, node_id: str) -> ResourceDescriptor:
        """Get total node resource capacity"""
        
    def get_node_utilization(self, node_id: str) -> ResourceUsage:
        """Get current resource utilization"""
        
    def reserve_resources(self, node_id: str, 
                         resources: ResourceDescriptor) -> ReservationID:
        """Reserve resources for future use"""
```

#### Developer Considerations

1. **Consistency**: NodeDB uses eventual consistency; check timestamps for freshness
2. **Fault Tolerance**: Nodes may become temporarily unreachable; implement retry logic
3. **Privacy**: Only public information is stored; sensitive data remains local
4. **Scalability**: Database is distributed; avoid central bottlenecks
5. **Security**: All updates are authenticated via P2P layer

### 3.6 REST API (`simaas/rest/`)

#### Purpose
HTTP-based API layer for external system integration, web interfaces, and client applications.

#### Architecture Overview
**Pattern**: FastAPI-based REST service with cryptographic authentication

```python
class RESTService:
    """FastAPI-based REST API service"""
    
    def __init__(self, node: Node, auth_handler: AuthHandler)
    def startup(self, address: Tuple[str, int]) -> None
    def add_custom_endpoints(self, router: APIRouter) -> None
```

#### Authentication System
**Architecture**: Signature-based authentication with identity verification

```python
class AuthHandler:
    """Cryptographic authentication for REST API"""
    
    def authenticate_request(self, request: Request) -> Identity:
        """Verify request signature and return identity"""
        
        # Extract signature components
        signature = request.headers.get("X-Signature")
        timestamp = request.headers.get("X-Timestamp") 
        identity_id = request.headers.get("X-Identity-ID")
        
        # Verify signature
        identity = self.nodedb.get_identity(identity_id)
        if not identity.verify_signature(request_data, signature):
            raise AuthenticationError("Invalid signature")
            
        return identity
```

#### API Endpoints

##### Data Object Repository Endpoints
```python
@router.post("/dor/objects")
async def add_data_object(file: UploadFile, metadata: DataObjectMetadata,
                         auth: Identity = Depends(authenticate)) -> DataObject:
    """Upload new data object to repository"""

@router.get("/dor/objects")
async def search_data_objects(query: SearchQuery,
                             auth: Identity = Depends(authenticate)) -> List[DataObject]:
    """Search for data objects"""

@router.get("/dor/objects/{obj_id}/content")
async def download_data_object(obj_id: str,
                              auth: Identity = Depends(authenticate)) -> StreamingResponse:
    """Download data object content"""
```

##### Runtime Infrastructure Endpoints
```python
@router.post("/rti/jobs")
async def submit_job(task: Task, 
                    auth: Identity = Depends(authenticate)) -> Job:
    """Submit computational job"""

@router.get("/rti/jobs/{job_id}/status")
async def get_job_status(job_id: str,
                        auth: Identity = Depends(authenticate)) -> JobStatus:
    """Get job execution status"""

@router.delete("/rti/jobs/{job_id}")
async def cancel_job(job_id: str,
                    auth: Identity = Depends(authenticate)) -> JobStatus:
    """Cancel running job"""
```

##### Network Management Endpoints
```python
@router.get("/network/nodes")
async def list_network_nodes(auth: Identity = Depends(authenticate)) -> List[NodeInfo]:
    """List known network nodes"""

@router.get("/network/status")
async def get_network_status(auth: Identity = Depends(authenticate)) -> NetworkStatus:
    """Get overall network health status"""
```

#### Client SDK Integration
**Architecture**: Proxy pattern for client-side API access

```python
class RESTClient:
    """Client SDK for REST API access"""
    
    def __init__(self, base_url: str, keystore: Keystore)
    
    def _sign_request(self, method: str, url: str, data: bytes) -> Dict[str, str]:
        """Generate authentication headers"""
        timestamp = str(get_timestamp_now())
        message = f"{method}:{url}:{timestamp}:{data.hex()}"
        signature = self.keystore.signing_key.sign(message.encode())
        
        return {
            "X-Identity-ID": self.keystore.identity.id,
            "X-Timestamp": timestamp,
            "X-Signature": signature.hex()
        }
    
    async def submit_job(self, task: Task) -> Job:
        """Submit job via REST API"""
        response = await self.session.post(
            f"{self.base_url}/rti/jobs",
            json=task.model_dump(),
            headers=self._sign_request("POST", "/rti/jobs", task_data)
        )
        return Job.model_validate(response.json())
```

#### Error Handling
**Architecture**: Structured error responses with detailed context

```python
class APIError(Exception):
    """Base API error with HTTP status and details"""
    def __init__(self, status_code: int, message: str, details: dict = None)

@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "details": exc.details,
            "timestamp": get_timestamp_now()
        }
    )
```

#### Developer Considerations

1. **Authentication**: All endpoints require valid cryptographic signatures
2. **Rate Limiting**: Implement appropriate rate limits for resource-intensive operations
3. **Streaming**: Use streaming responses for large data downloads
4. **Versioning**: API versioning strategy for backward compatibility
5. **Documentation**: Automatic OpenAPI documentation generation

### 3.7 Command Line Interface (`simaas/cli/`)

#### Purpose
Comprehensive command-line interface providing user-friendly access to all platform capabilities with interactive workflows and batch operations.

#### Architecture Overview
**Pattern**: Command pattern with hierarchical organization and interactive components

```python
class CLIParser:
    """Main CLI parser with command hierarchy"""
    
    def __init__(self, description: str, arguments: List[Argument])
    def add_command_group(self, name: str, group: CLICommandGroup) -> None
    def parse_and_execute(self, args: List[str]) -> None

class CLICommand(ABC):
    """Base class for individual CLI commands"""
    
    @abstractmethod
    def execute(self, args: argparse.Namespace) -> None
```

#### Command Organization
**Hierarchy**: Domain-specific command groups with consistent patterns

```
simaas-cli
├── identity          # Identity and credential management
│   ├── create        # Create new identity
│   ├── list          # List available identities
│   ├── show          # Display identity details
│   └── credentials   # Credential management
│       ├── add       # Add SSH/GitHub credentials
│       ├── list      # List stored credentials
│       └── test      # Test credential validity
├── service           # Node service management
│   ├── start         # Start node services
│   ├── stop          # Stop node services
│   └── status        # Show service status
├── dor               # Data Object Repository operations
│   ├── add           # Upload data objects
│   ├── search        # Search for objects
│   ├── download      # Download object content
│   ├── access        # Manage access permissions
│   └── tag           # Tag management
├── rti               # Runtime Infrastructure operations
│   ├── proc          # Processor management
│   │   ├── deploy    # Deploy processors
│   │   ├── list      # List deployed processors
│   │   └── undeploy  # Undeploy processors
│   └── job           # Job management
│       ├── submit    # Submit computational jobs
│       ├── status    # Check job status
│       ├── list      # List jobs
│       └── cancel    # Cancel running jobs
├── network           # Network operations
│   └── list          # List network nodes
└── proc-builder      # Processor image building
    ├── local         # Build from local directory
    └── github        # Build from GitHub repository
```

#### Interactive User Experience
**Pattern**: Progressive disclosure with intelligent prompts

```python
class InteractiveHelper:
    """Rich interactive prompts for user-friendly experience"""
    
    def select_keystore(self, available_keystores: List[str]) -> str:
        """Interactive keystore selection"""
        return inquirer.select(
            message="Select keystore:",
            choices=available_keystores,
            default=available_keystores[0] if len(available_keystores) == 1 else None
        ).execute()
    
    def select_data_object(self, objects: List[DataObject], 
                          purpose: str) -> DataObject:
        """Interactive data object selection with rich display"""
        choices = [
            {
                "name": f"{obj.obj_id[:8]} - {obj.data_type} ({obj.data_format})",
                "value": obj
            }
            for obj in objects
        ]
        
        return inquirer.select(
            message=f"Select data object for {purpose}:",
            choices=choices
        ).execute()
```

#### Keystore Management Integration
**Architecture**: Seamless integration with cryptographic identity system

```python
class KeystoreManager:
    """CLI integration for keystore operations"""
    
    def load_keystore(self, keystore_path: str, keystore_id: str = None, 
                     password: str = None) -> Keystore:
        """Load keystore with interactive prompts if needed"""
        
        # Auto-discovery of available keystores
        available = Keystore.discover(keystore_path)
        
        if not keystore_id and len(available) > 1:
            keystore_id = self.interactive.select_keystore(available)
        elif len(available) == 1:
            keystore_id = available[0]
            
        # Interactive password prompt
        if not password:
            password = inquirer.secret(
                message=f"Enter password for keystore '{keystore_id}':"
            ).execute()
            
        return Keystore.load(keystore_path, keystore_id, password)
```

#### Service Integration Patterns
**Architecture**: Proxy pattern for service access with error handling

```python
class ServiceCommandBase(CLICommand):
    """Base class for service-interacting commands"""
    
    def get_service_proxy(self, service_type: str, 
                         rest_address: str = None) -> ServiceProxy:
        """Get proxy for service interaction"""
        
        if rest_address:
            # Remote service access
            return ServiceProxy(rest_address, self.keystore)
        else:
            # Local service access (check if running)
            local_address = self.discover_local_service(service_type)
            if local_address:
                return ServiceProxy(local_address, self.keystore)
            else:
                raise CLIRuntimeError(f"No running {service_type} service found")
```

#### Batch Operations and Scripting
**Architecture**: Support for non-interactive batch processing

```python
class BatchProcessor:
    """Support for batch operations and scripting"""
    
    def process_config_file(self, config_path: str) -> None:
        """Process batch operations from configuration file"""
        
    def export_results(self, results: List[Any], format: str, 
                      output_path: str) -> None:
        """Export results in various formats (JSON, CSV, YAML)"""
        
    def validate_inputs(self, inputs: Dict[str, Any], 
                       schema: dict) -> None:
        """Validate inputs against JSON schema"""
```

#### Progress Reporting and Feedback
**Architecture**: Rich progress indicators for long-running operations

```python
class ProgressReporter:
    """Rich progress reporting for CLI operations"""
    
    def track_job_execution(self, job_id: str, 
                           rti_proxy: RTIProxy) -> JobStatus:
        """Real-time job status tracking with progress bar"""
        
        with Progress() as progress:
            task = progress.add_task("Executing job...", total=100)
            
            while True:
                status = rti_proxy.get_job_status(job_id)
                progress.update(task, completed=status.progress)
                
                if status.state in [JobStatus.State.SUCCESSFUL, 
                                  JobStatus.State.FAILED, 
                                  JobStatus.State.CANCELLED]:
                    break
                    
                time.sleep(1)
        
        return status
```

#### Developer Considerations

1. **User Experience**: Prioritize interactive workflows with sensible defaults
2. **Error Handling**: Provide clear, actionable error messages with suggestions
3. **Configuration**: Support both interactive and configuration-file-based workflows
4. **Consistency**: Maintain consistent command patterns across all domains
5. **Documentation**: Built-in help and examples for all commands

## 4. Integration Patterns and Extension Points

### Processor Development Framework

#### Standard Development Workflow
1. **Implement Processor Class**: Inherit from `ProcessorBase` and implement required methods
2. **Define Interface Contract**: Create `descriptor.json` with input/output specifications
3. **Create Dockerfile**: Standardized multi-stage build with middleware integration
4. **Test Locally**: Use CLI tools for local testing and debugging
5. **Build and Deploy**: Use processor builder to create Docker images and deploy to network

#### Extension Patterns
- **Custom Resource Types**: Extend `ResourceDescriptor` for specialized hardware requirements
- **Communication Protocols**: Implement custom inter-processor communication patterns
- **Data Validators**: Custom validation logic for input/output data objects
- **Progress Reporting**: Rich progress reporting with custom metrics and visualizations

### Service Extension Framework

#### Custom P2P Protocols
```python
class CustomProtocol(P2PProtocol):
    """Custom protocol for domain-specific operations"""
    
    def handle_request(self, sender_iid: str, request: dict) -> dict:
        # Custom protocol logic
        pass
        
    def get_request_schema(self) -> dict:
        return {"type": "object", "properties": {...}}
```

#### Custom REST Endpoints
```python
def add_custom_endpoints(rest_service: RESTService) -> None:
    """Add domain-specific REST endpoints"""

    router = APIRouter()

    @router.get("/custom/endpoint")
    async def custom_endpoint(auth: Identity = Depends(authenticate)):
        return {"custom": "response"}

    rest_service.add_custom_endpoints(router)
```

### DOR/RTI Plugin Development

The DOR (Data Object Repository) and RTI (Runtime Infrastructure) services are implemented
as plugins, allowing custom implementations for different storage backends or execution
environments.

#### Plugin Directory Structure
Plugins are organized in the `plugins/` directory with the following structure:
```
plugins/
  dor_default/           # DOR plugin using SQLite
    __init__.py          # Exports the service class
    service.py           # Implementation
    requirements.txt     # Plugin-specific dependencies
  rti_docker/            # RTI plugin for local Docker execution
    __init__.py
    service.py
    requirements.txt
  rti_aws/               # RTI plugin for AWS Batch execution
    __init__.py
    service.py
    requirements.txt
```

#### Creating a DOR Plugin
DOR plugins must implement the `DORInterface` and provide a `plugin_name()` class method:

```python
# plugins/dor_custom/__init__.py
from .service import CustomDORService
__all__ = ['CustomDORService']

# plugins/dor_custom/service.py
from simaas.dor.api import DORInterface

class CustomDORService(DORInterface):
    @classmethod
    def plugin_name(cls) -> str:
        return "custom"  # Name shown in CLI selection

    def __init__(self, node, db_path: str):
        # Initialize your storage backend
        pass

    # Implement all DORInterface methods...
```

#### Creating an RTI Plugin
RTI plugins must extend `RTIServiceBase` and provide a `plugin_name()` class method:

```python
# plugins/rti_custom/__init__.py
from .service import CustomRTIService
__all__ = ['CustomRTIService']

# plugins/rti_custom/service.py
from simaas.rti.base import RTIServiceBase

class CustomRTIService(RTIServiceBase):
    @classmethod
    def plugin_name(cls) -> str:
        return "custom"  # Name shown in CLI selection

    def __init__(self, node, db_path: str, retain_job_history: bool = False,
                 strict_deployment: bool = True):
        super().__init__(node, db_path, retain_job_history, strict_deployment)

    # Override methods for custom execution environment...
```

#### Plugin Discovery
Plugins are discovered automatically at startup from:
1. The built-in `plugins/` directory in the Sim-aaS Middleware repository
2. Additional directories specified via `--plugins` CLI argument

The plugin name (returned by `plugin_name()`) is used for CLI selection. Plugin folder
names should use underscores (e.g., `dor_postgres`, `rti_kubernetes`).

### Security and Compliance Extensions

#### Custom Authentication Providers
- **LDAP Integration**: Enterprise directory service integration
- **OAuth2/OIDC**: Web-based authentication flows
- **Hardware Security Modules**: HSM-based key storage and operations
- **Multi-Factor Authentication**: Additional authentication factors

#### Audit and Compliance
- **Audit Logging**: Comprehensive audit trails for all operations
- **Compliance Reporting**: Automated compliance checking and reporting
- **Data Governance**: Policies for data retention, privacy, and access control
- **Regulatory Compliance**: GDPR, HIPAA, and other regulatory framework support

This comprehensive developer documentation provides the foundation for understanding, extending, and contributing to the Sim-aaS Middleware platform. The architecture's modular design and well-defined interfaces enable both current functionality and future enhancements while maintaining security, scalability, and usability principles.

## 5. Testing

### 5.1 Environment Setup

#### Virtual Environment
Tests must be run using the project's virtual environment, not system Python:

```bash
# Create virtual environment (if not exists)
python3.11 -m venv .venv

# Install core dependencies (includes plugin dependencies like boto3 for AWS)
.venv/bin/pip install -r requirements.txt

# Install the package in editable mode
.venv/bin/pip install -e .
```

#### Built-in Plugins
The repository includes three built-in plugins in the `plugins/` directory:

| Plugin | Purpose | Additional Dependencies |
|--------|---------|------------------------|
| `dor_default` | Default DOR using SQLite | None (uses core deps) |
| `rti_docker` | Local Docker execution | None (uses core deps) |
| `rti_aws` | AWS Batch execution | `boto3` (included in requirements.txt) |

These plugins are automatically discovered at runtime. No separate installation is needed.

#### Environment Variables
Create a `.env` file in the project root. The test framework automatically loads it via `python-dotenv`.

### 5.2 Environment Variables Reference

#### Required for All Tests (Docker RTI)

| Variable | Description | How to Obtain |
|----------|-------------|---------------|
| `SIMAAS_REPO_PATH` | Absolute path to the sim-aas-middleware repository | Set to your local clone path (e.g., `/home/user/sim-aas-middleware`) |

#### Optional for Docker RTI

| Variable | Description | How to Obtain |
|----------|-------------|---------------|
| `SIMAAS_RTI_DOCKER_SCRATCH_PATH` | Path for Docker scratch volumes | Any writable directory path |

#### Required for AWS RTI Tests

See [Running a Sim-aaS Node - AWS RTI Service](docs/usage_run_simaas_node.md#aws-rti-service) for detailed AWS setup instructions.

| Variable | Description | How to Obtain |
|----------|-------------|---------------|
| `SIMAAS_AWS_REGION` | AWS region (e.g., `ap-southeast-1`) | Choose based on your AWS Batch setup |
| `SIMAAS_AWS_ACCESS_KEY_ID` | AWS access key ID | Create via AWS IAM Console |
| `SIMAAS_AWS_SECRET_ACCESS_KEY` | AWS secret access key | Create via AWS IAM Console |
| `SIMAAS_AWS_ROLE_ARN` | IAM role ARN for Batch execution | Create IAM role with required permissions (see docs) |
| `SIMAAS_AWS_JOB_QUEUE` | AWS Batch job queue name | Create via AWS Batch Console |

#### AWS RTI Testing Workarounds (for local development)

These variables enable SSH tunneling to test AWS RTI from a local machine. See [Important Notes on Testing with AWS RTI Service](docs/usage_run_simaas_node.md#important-notes-on-testing-with-aws-rti-service).

| Variable | Description | How to Obtain |
|----------|-------------|---------------|
| `SSH_TUNNEL_HOST` | EC2 instance public DNS | AWS EC2 Console (Public IPv4 DNS) |
| `SSH_TUNNEL_USER` | SSH username for EC2 | Typically `ubuntu` or `ec2-user` |
| `SSH_TUNNEL_KEY_PATH` | Path to SSH private key | Your `.pem` key file from EC2 |
| `SIMAAS_CUSTODIAN_HOST` | EC2 instance private DNS | AWS EC2 Console (Private IPv4 DNS) |

#### GitHub Credentials (for building images from GitHub)

| Variable | Description | How to Obtain |
|----------|-------------|---------------|
| `GITHUB_USERNAME` | GitHub username | Your GitHub account username |
| `GITHUB_TOKEN` | GitHub Personal Access Token | GitHub Settings → Developer settings → Personal access tokens |

#### Example `.env` File Structure

```bash
# Required for Docker RTI tests
SIMAAS_REPO_PATH=/path/to/sim-aas-middleware

# GitHub credentials (optional, for GitHub-based image builds)
GITHUB_USERNAME=your-username
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# AWS RTI (optional, only needed for AWS tests)
SIMAAS_AWS_REGION=ap-southeast-1
SIMAAS_AWS_ACCESS_KEY_ID=AKIA...
SIMAAS_AWS_SECRET_ACCESS_KEY=...
SIMAAS_AWS_ROLE_ARN=arn:aws:iam::123456789:role/simaas-role
SIMAAS_AWS_JOB_QUEUE=simaas-queue

# AWS SSH Tunneling (optional, for local AWS RTI testing)
SSH_TUNNEL_HOST=ec2-xx-xx-xx-xx.region.compute.amazonaws.com
SSH_TUNNEL_USER=ubuntu
SSH_TUNNEL_KEY_PATH=/path/to/key.pem
SIMAAS_CUSTODIAN_HOST=ip-xx-xx-xx-xx.region.compute.internal
```

### 5.3 Running Tests

#### Prerequisites
- Docker must be running (required for RTI tests)
- Virtual environment activated or use `.venv/bin/python` prefix

#### Running All Tests

```bash
# Run all tests (approximately 15-20 minutes)
.venv/bin/python -m pytest simaas/tests/ -v

# Run with coverage
.venv/bin/python -m coverage run -m pytest simaas/tests/ -v
.venv/bin/python -m coverage report
```

#### Running Individual Test Files

```bash
# Run a specific test file
.venv/bin/python -m pytest simaas/tests/test_core.py -v

# Run a specific test function
.venv/bin/python -m pytest simaas/tests/test_core.py::test_ec_signing -v
```

### 5.4 Test Categories

#### Fast Tests (~10-60 seconds each)
| Test File | Tests | Description |
|-----------|-------|-------------|
| `test_core.py` | 14 | Core functionality (crypto, keystore, logging) |
| `test_service_rest.py` | 7 | REST API operations |
| `test_service_namespace.py` | 5 | Namespace service |
| `test_service_p2p.py` | 7 | P2P networking |
| `test_service_dor.py` | 16 | Data Object Repository |
| `test_example_abc.py` | 4 | ABC processor example |
| `test_example_ping.py` | 5 | Ping processor example |
| `test_example_primes.py` | 5 | Prime factorization example |
| `test_example_cosim.py` | 2 | Co-simulation example |

#### Medium Tests (~1-2 minutes each)
| Test File | Tests | Description |
|-----------|-------|-------------|
| `test_service_nodedb.py` | 13 | Node database operations |

#### Longer Tests (~4-7 minutes each)
| Test File | Tests | Description |
|-----------|-------|-------------|
| `test_service_rti.py` | 13 | RTI service (Docker) |
| `test_cli.py` | 32 | CLI commands |
| `test_service_rti_aws.py` | 8 | RTI service (AWS Batch) |

**Total: 108 tests across 13 test files**

### 5.5 Notes

- **Docker Requirement**: RTI-related tests require Docker to be running
- **AWS Tests**: Will run with mock/fallback behavior if AWS credentials are not configured; full execution requires valid AWS setup
- **GitHub Credentials**: Required for tests that build processor images from GitHub repositories
- **IDE Support**: Tests also work in PyCharm and other IDEs with pytest support