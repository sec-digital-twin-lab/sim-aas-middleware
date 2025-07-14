# Sim-aaS Middleware Developer Documentation

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Services](#services)
5. [CLI Interface](#cli-interface)
6. [Processor Development](#processor-development)
7. [API Reference](#api-reference)
8. [Development Guidelines](#development-guidelines)
9. [Testing](#testing)
10. [Deployment](#deployment)

## Overview

The **Simulation-as-a-Service (Sim-aaS) Middleware** is a distributed computing platform designed to facilitate deployment and operations of federations of computational models. It provides a secure, scalable infrastructure for running simulations across multiple nodes in a peer-to-peer network.

### Key Features
- **Distributed Computing**: P2P network of nodes for scalable computation
- **Secure Identity Management**: Cryptographic keystores for authentication and authorization
- **Data Object Repository (DOR)**: Secure storage and sharing of data objects
- **Runtime Infrastructure (RTI)**: Containerized execution of processors
- **Processor Framework**: Standardized interface for computational models
- **REST and P2P APIs**: Multiple communication protocols

### Technology Stack
- **Language**: Python 3.12
- **Web Framework**: FastAPI (REST API)
- **Message Queue**: ZeroMQ (P2P communication)
- **Containerization**: Docker
- **Database**: SQLite (with SQLAlchemy ORM)
- **Cryptography**: RSA/EC key pairs, AES encryption
- **Cloud Integration**: AWS support

## Architecture

### High-Level Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Node A        │    │   Node B        │    │   Node C        │
│                 │    │                 │    │                 │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────┐ │
│ │    REST     │ │    │ │    REST     │ │    │ │    REST     │ │
│ │   Service   │ │    │ │   Service   │ │    │ │   Service   │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ └─────────────┘ │
│                 │    │                 │    │                 │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────┐ │
│ │    P2P      │◄────►│ │    P2P      │◄────►│ │    P2P      │ │
│ │   Service   │ │    │ │   Service   │ │    │ │   Service   │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ └─────────────┘ │
│                 │    │                 │    │                 │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────┐ │
│ │     DOR     │ │    │ │     RTI     │ │    │ │     DOR     │ │
│ │   Service   │ │    │ │   Service   │ │    │ │   Service   │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ └─────────────┘ │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Node Types

1. **Storage Node**: Provides DOR service only
2. **Execution Node**: Provides RTI service only  
3. **Full Node**: Provides both DOR and RTI services
4. **Network Node**: Provides only P2P communication

### Communication Protocols

- **REST API**: HTTP-based API for client interactions
- **P2P Protocol**: ZeroMQ-based peer-to-peer communication
- **Container Communication**: TCP sockets for job runner communication

## Core Components

### 1. Keystore and Identity Management (`simaas/core/`)

The keystore is the foundation of security in the Sim-aaS system.

#### Key Classes
- `Keystore`: Main keystore class managing cryptographic assets
- `Identity`: Public identity information
- `RSAKeyPair`: RSA key pair implementation
- `ECKeyPair`: Elliptic curve key pair implementation

#### Keystore Contents
```python
{
    "iid": "unique_identity_id",
    "profile": {"name": "User Name", "email": "user@example.com"},
    "nonce": 1234567890,
    "signature": "cryptographic_signature",
    "assets": {
        "master-key": {...},
        "signing-key": {...},
        "encryption-key": {...},
        "content-keys": {...},
        "ssh-credentials": {...},
        "github-credentials": {...}
    }
}
```

#### Usage Example
```python
from simaas.core.keystore import Keystore

# Create new keystore
keystore = Keystore.new("John Doe", "john@example.com", password="secret")

# Load existing keystore
keystore = Keystore.from_file("path/to/keystore.json", password="secret")

# Encrypt/decrypt data
encrypted = keystore.encrypt(b"sensitive data")
decrypted = keystore.decrypt(encrypted)

# Sign/verify data
signature = keystore.sign(b"message")
is_valid = keystore.verify(b"message", signature)
```

### 2. Node Management (`simaas/node/`)

Nodes are the fundamental building blocks of the Sim-aaS network.

#### Key Classes
- `Node`: Abstract base class for all nodes
- `DefaultNode`: Concrete implementation of a node

#### Node Configuration
```python
from simaas.node.default import DefaultNode, DORType, RTIType

node = DefaultNode.create(
    keystore=keystore,
    storage_path="/path/to/datastore",
    p2p_address="localhost:6000",
    rest_address=("localhost", 8000),
    dor_type=DORType.BASIC,
    rti_type=RTIType.DOCKER
)
```

### 3. Data Object Repository (DOR) (`simaas/dor/`)

The DOR provides secure storage and sharing of data objects across the network.

#### Key Classes
- `DefaultDORService`: Main DOR implementation
- `DataObject`: Data object representation
- `DataObjectRecipe`: Provenance tracking

#### Data Object Structure
```python
{
    "obj_id": "unique_object_id",
    "c_hash": "content_hash",
    "data_type": "JSONObject",
    "data_format": "json",
    "created": {"timestamp": 1234567890, "creator": "user_id"},
    "owner_iid": "owner_identity_id",
    "access_restricted": True,
    "access": {"user1": "read", "user2": "write"},
    "tags": {"category": "simulation", "version": "1.0"},
    "custodian": {"node_info": {...}},
    "content_encrypted": True,
    "license": "MIT"
}
```

#### Usage Example
```python
from simaas.dor.default import DefaultDORService

# Add data object
data_object = dor_service.add(
    content_path="/path/to/file",
    data_type="JSONObject",
    data_format="json",
    owner_iid="user_id"
)

# Search data objects
results = dor_service.search(
    patterns=["simulation"],
    data_type="JSONObject"
)

# Grant access
dor_service.grant_access("obj_id", "user_id")
```

### 4. Runtime Infrastructure (RTI) (`simaas/rti/`)

The RTI manages the execution of processors in containerized environments.

#### Key Classes
- `DefaultRTIService`: Docker-based RTI implementation
- `AWSRTIService`: AWS-based RTI implementation
- `Processor`: Processor representation
- `Job`: Job representation

#### RTI Types
- **Docker**: Local container execution
- **AWS**: Cloud-based execution
- **None**: No execution capability

#### Usage Example
```python
from simaas.rti.default import DefaultRTIService

# Deploy processor
rti_service.perform_deploy(processor)

# Submit job
rti_service.perform_submit_single(job, processor)

# Get job status
status = rti_service.get_job_status(job_id)
```

### 5. P2P Communication (`simaas/p2p/`)

P2P communication enables distributed coordination between nodes.

#### Key Classes
- `P2PService`: Main P2P service implementation
- `P2PProtocol`: Protocol base class
- `P2PMessage`: Message representation

#### Supported Protocols
- `P2PUpdateIdentity`: Identity updates
- `P2PJoinNetwork`: Network joining
- `P2PLookupDataObject`: Data object discovery
- `P2PFetchDataObject`: Data object retrieval
- `P2PPushDataObject`: Data object sharing
- `P2PPushJobStatus`: Job status updates

#### Usage Example
```python
from simaas.p2p.service import P2PService
from simaas.p2p.protocol import P2PLookupDataObject

# Add protocol
p2p_service.add(P2PLookupDataObject(node))

# Start service
p2p_service.start_service()
```

### 6. REST API (`simaas/rest/`)

REST API provides HTTP-based access to node services.

#### Key Classes
- `RESTService`: Main REST service implementation
- `RESTApp`: FastAPI application wrapper

#### API Endpoints
- `/api/v1/dor/*`: Data Object Repository endpoints
- `/api/v1/rti/*`: Runtime Infrastructure endpoints
- `/api/v1/node/*`: Node management endpoints

#### Authentication
REST endpoints use keystore-based authentication:
```python
from simaas.rest.auth import make_depends

# Add authentication to endpoint
dependencies = make_depends(endpoint_function, node)
```

## Services

### 1. Data Object Repository (DOR) Service

**Purpose**: Secure storage and sharing of data objects

**Key Features**:
- Content-addressable storage
- Access control and permissions
- Provenance tracking
- Network-wide search and discovery
- Encryption support

**API Endpoints**:
```python
# Add data object
POST /api/v1/dor/add

# Search data objects
GET /api/v1/dor/search

# Get data object metadata
GET /api/v1/dor/meta/{obj_id}

# Download data object content
GET /api/v1/dor/content/{obj_id}

# Grant/revoke access
POST /api/v1/dor/access/grant
POST /api/v1/dor/access/revoke
```

### 2. Runtime Infrastructure (RTI) Service

**Purpose**: Containerized execution of processors

**Key Features**:
- Docker container management
- Job scheduling and monitoring
- Resource allocation
- Process isolation
- AWS integration

**API Endpoints**:
```python
# Deploy/undeploy processors
POST /api/v1/rti/proc/deploy
DELETE /api/v1/rti/proc/undeploy

# Submit/cancel jobs
POST /api/v1/rti/job/submit
DELETE /api/v1/rti/job/cancel

# Get job status
GET /api/v1/rti/job/status/{job_id}

# List processors/jobs
GET /api/v1/rti/proc/list
GET /api/v1/rti/job/list
```

### 3. Node Database Service

**Purpose**: Network topology and identity management

**Key Features**:
- Node discovery and registration
- Identity management
- Network topology tracking
- Service availability tracking

**API Endpoints**:
```python
# Update node identity
POST /api/v1/node/identity/update

# Get network information
GET /api/v1/node/network

# Get node identities
GET /api/v1/node/identities
```

## CLI Interface

The Sim-aaS CLI provides comprehensive command-line access to all functionality.

### Main Commands

#### Identity Management
```bash
# Create new identity
simaas-cli identity create --name "John Doe" --email "john@example.com"

# List identities
simaas-cli identity list

# Show identity details
simaas-cli identity show --keystore-id <id>

# Publish identity to node
simaas-cli identity publish --address localhost:8000
```

#### Credential Management
```bash
# Add SSH credentials
simaas-cli identity credentials add ssh --name "server1" --host "192.168.1.100"

# Add GitHub credentials
simaas-cli identity credentials add github --repository "user/repo"

# Test credentials
simaas-cli identity credentials test ssh --name "server1"
simaas-cli identity credentials test github --repository "user/repo"
```

#### Data Object Repository
```bash
# Add data object
simaas-cli dor add --address localhost:8000 --file data.json --type JSONObject --format json

# Search data objects
simaas-cli dor search --address localhost:8000 --pattern "simulation"

# Download data object
simaas-cli dor download --address localhost:8000 --obj-id <id> --output file.json

# Grant access
simaas-cli dor access grant --address localhost:8000 --obj-id <id> --user <user_id>
```

#### Runtime Infrastructure
```bash
# Deploy processor
simaas-cli rti proc deploy --address localhost:8000 --proc-id <id>

# Submit job
simaas-cli rti job submit --address localhost:8000 --proc-id <id> --input a.json --input b.json

# Get job status
simaas-cli rti job status --address localhost:8000 --job-id <id>

# List processors/jobs
simaas-cli rti proc list --address localhost:8000
simaas-cli rti job list --address localhost:8000
```

#### Processor Building
```bash
# Build local processor
simaas-cli proc-build local --path /path/to/processor --output image.tar

# Build from GitHub
simaas-cli proc-build github --repository user/repo --proc-path processor --commit main
```

#### Network Management
```bash
# List network nodes
simaas-cli network list --address localhost:8000
```

### Service Commands

#### Run Node
```bash
# Run a node instance
simaas-node --keystore-id <id> --password <password> run \
    --datastore /path/to/datastore \
    --rest-address localhost:8000 \
    --p2p-address localhost:6000 \
    --dor-type basic \
    --rti-type docker
```

#### Job Runner
```bash
# Run a job
simaas-cli run --job-path /job --proc-path /processor --service-address tcp://0.0.0.0:6000
```

## Processor Development

### Processor Structure

A processor consists of three main files:

```
processor/
├── descriptor.json    # Interface specification
├── processor.py       # Implementation
└── Dockerfile         # Container definition
```

### 1. Processor Descriptor (`descriptor.json`)

Defines the input/output interface and metadata:

```json
{
  "name": "my-processor",
  "input": [
    {
      "name": "input_data",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "value": {"type": "number"}
        },
        "required": ["value"]
      }
    }
  ],
  "output": [
    {
      "name": "result",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "result": {"type": "number"}
        },
        "required": ["result"]
      }
    }
  ],
  "required_secrets": ["API_KEY"]
}
```

### 2. Processor Implementation (`processor.py`)

Must inherit from `ProcessorBase` and implement required methods:

```python
from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace
from simaas.rti.schemas import Job
import logging
import json
import os

class MyProcessor(ProcessorBase):
    def __init__(self, proc_path: str) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(
        self,
        wd_path: str,
        job: Job,
        listener: ProgressListener,
        namespace: Namespace,
        logger: logging.Logger
    ) -> None:
        # Read input
        with open(os.path.join(wd_path, 'input_data'), 'r') as f:
            input_data = json.load(f)
        
        # Process data
        result = self._process(input_data['value'])
        
        # Write output
        with open(os.path.join(wd_path, 'result'), 'w') as f:
            json.dump({'result': result}, f)
        
        # Notify output availability
        listener.on_output_available('result')
        listener.on_progress_update(100)

    def interrupt(self) -> None:
        self._is_cancelled = True

    def _process(self, value: float) -> float:
        return value * 2
```

### 3. Dockerfile

Defines the container environment:

```dockerfile
FROM python:3.12.10-slim-bullseye AS builder

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git build-essential libffi-dev

# Create virtual environment
RUN python -m venv /opt/venv
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel

# Copy processor and install dependencies
COPY . /processor
RUN /opt/venv/bin/pip install /processor/sim-aas-middleware
RUN /opt/venv/bin/pip install -r /processor/requirements.txt

# Runtime stage
FROM python:3.12.10-slim-bullseye
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /processor /processor

ENV PATH="/opt/venv/bin:${PATH}"

RUN mkdir /job
EXPOSE 6000

ENTRYPOINT ["simaas-cli", "--log-console", "run", \
            "--job-path", "/job", \
            "--proc-path", "/processor", \
            "--service-address", "tcp://0.0.0.0:6000"]
```

### Processor Types

#### 1. Pure Processor
- Contains complete business logic
- Written in Python
- Self-contained implementation

#### 2. Processor Adapter
- Wraps external applications
- Provides interface translation
- Manages external tool execution

### Best Practices

1. **Error Handling**: Implement proper error handling and logging
2. **Progress Reporting**: Use the progress listener to report status
3. **Cancellation Support**: Check cancellation flag periodically
4. **Resource Management**: Clean up resources properly
5. **Input Validation**: Validate inputs before processing
6. **Output Notification**: Notify when outputs are available

## API Reference

### Core Classes

#### Keystore
```python
class Keystore:
    @classmethod
    def new(cls, name: str, email: str, path: str, password: str) -> Keystore
    
    @classmethod
    def from_file(cls, path: str, password: str) -> Keystore
    
    def encrypt(self, content: bytes) -> bytes
    def decrypt(self, content: bytes) -> bytes
    def sign(self, message: bytes) -> str
    def verify(self, message: bytes, signature: str) -> bool
    def sync(self) -> None
```

#### Node
```python
class Node(abc.ABC):
    def startup(self, p2p_address: str, rest_address: Tuple[str, int] = None,
                boot_node_address: Tuple[str, int] = None, bind_all_address: bool = False) -> None
    
    def shutdown(self) -> None
    
    @property
    def keystore(self) -> Keystore
    @property
    def identity(self) -> Identity
    @property
    def datastore(self) -> str
```

#### Processor
```python
class ProcessorBase(abc.ABC):
    def run(self, wd_path: str, job: Job, listener: ProgressListener,
            namespace: Namespace, logger: Logger) -> None
    
    def interrupt(self) -> None
    
    @property
    def descriptor(self) -> ProcessorDescriptor
```

### Service Interfaces

#### DOR Service
```python
class DORService(abc.ABC):
    def add(self, content_path: str, data_type: str, data_format: str,
            owner_iid: str, **kwargs) -> DataObject
    
    def search(self, patterns: List[str] = None, owner_iid: str = None,
               data_type: str = None, data_format: str = None) -> List[DataObject]
    
    def get_meta(self, obj_id: str) -> Optional[DataObject]
    def get_content(self, obj_id: str, content_path: str) -> None
    def remove(self, obj_id: str) -> Optional[DataObject]
```

#### RTI Service
```python
class RTIService(abc.ABC):
    def perform_deploy(self, proc: Processor) -> None
    def perform_undeploy(self, proc: Processor, keep_image: bool = True) -> None
    def perform_submit_single(self, job: Job, proc: Processor) -> None
    def perform_submit_batch(self, batch: List[Tuple[Job, JobStatus, Processor]]) -> None
    def perform_cancel(self, job_id: str, peer_address: P2PAddress) -> None
```

### Data Models

#### DataObject
```python
class DataObject(BaseModel):
    obj_id: str
    c_hash: str
    data_type: str
    data_format: str
    created: Dict[str, Any]
    owner_iid: str
    access_restricted: bool
    access: Dict[str, str]
    tags: Dict[str, Any]
    custodian: NodeInfo
    content_encrypted: bool
    license: Optional[str]
```

#### Job
```python
class Job(BaseModel):
    id: str
    proc_id: str
    task: JobTask
    status: JobStatus
    created: Dict[str, Any]
    started: Optional[Dict[str, Any]]
    completed: Optional[Dict[str, Any]]
    error: Optional[str]
```

#### Processor
```python
class Processor(BaseModel):
    id: str
    state: ProcessorState
    image_name: Optional[str]
    ports: List[Tuple[int, str]]
    gpp: Optional[GitProcessorPointer]
    error: Optional[str]
```

## Development Guidelines

### Code Style

1. **Python Style**: Follow PEP 8 guidelines
2. **Type Hints**: Use type hints for all function parameters and return values
3. **Docstrings**: Provide comprehensive docstrings for all public methods
4. **Error Handling**: Use custom exceptions for domain-specific errors
5. **Logging**: Use structured logging with appropriate log levels

### Project Structure

```
simaas/
├── core/           # Core functionality (keystore, identity, etc.)
├── cli/            # Command-line interface
├── dor/            # Data Object Repository
├── rti/            # Runtime Infrastructure
├── p2p/            # Peer-to-peer communication
├── rest/           # REST API
├── node/           # Node management
├── nodedb/         # Node database
├── namespace/      # Namespace management
├── helpers.py      # Utility functions
├── service.py      # Service entry point
└── meta.py         # Package metadata
```

### Testing

1. **Unit Tests**: Test individual components in isolation
2. **Integration Tests**: Test component interactions
3. **End-to-End Tests**: Test complete workflows
4. **Mocking**: Use mocks for external dependencies

### Error Handling

1. **Custom Exceptions**: Define domain-specific exceptions
2. **Graceful Degradation**: Handle errors gracefully
3. **User-Friendly Messages**: Provide clear error messages
4. **Logging**: Log errors with appropriate context

### Security

1. **Input Validation**: Validate all inputs
2. **Authentication**: Use keystore-based authentication
3. **Authorization**: Implement proper access controls
4. **Encryption**: Encrypt sensitive data
5. **Secure Communication**: Use TLS for REST API

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_core.py

# Run with coverage
pytest --cov=simaas

# Run with verbose output
pytest -v
```

### Test Structure

```
tests/
├── conftest.py           # Test configuration and fixtures
├── test_cli.py           # CLI tests
├── test_core.py          # Core functionality tests
├── test_service_dor.py   # DOR service tests
├── test_service_rti.py   # RTI service tests
└── test_example_*.py     # Example processor tests
```

### Test Examples

```python
import pytest
from simaas.core.keystore import Keystore

def test_keystore_creation():
    keystore = Keystore.new("Test User", "test@example.com", password="secret")
    assert keystore.identity.name == "Test User"
    assert keystore.identity.email == "test@example.com"

def test_keystore_encryption():
    keystore = Keystore.new("Test User", "test@example.com", password="secret")
    data = b"sensitive data"
    encrypted = keystore.encrypt(data)
    decrypted = keystore.decrypt(encrypted)
    assert decrypted == data
```

## Deployment

### Prerequisites

1. **Python 3.12**: Required Python version
2. **Docker**: For containerized execution
3. **Git**: For processor versioning
4. **Network Access**: For P2P communication

### Installation

```bash
# Clone repository
git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware
cd sim-aas-middleware

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install package
pip install -e .
```

### Configuration

1. **Environment Variables**:
   ```bash
   export SIMAAS_REPO_PATH="/path/to/sim-aas-middleware"
   ```

2. **Keystore Setup**:
   ```bash
   simaas-cli identity create --name "Node Operator" --email "operator@example.com"
   ```

3. **Node Configuration**:
   ```bash
   simaas-node --keystore-id <id> --password <password> run \
       --datastore /path/to/datastore \
       --rest-address 0.0.0.0:8000 \
       --p2p-address 0.0.0.0:6000 \
       --dor-type basic \
       --rti-type docker
   ```

### Production Deployment

1. **Docker Deployment**:
   ```dockerfile
   FROM python:3.12.10-slim-bullseye
   COPY . /app
   WORKDIR /app
   RUN pip install -e .
   EXPOSE 8000 6000
   CMD ["simaas-node", "run"]
   ```

2. **Systemd Service**:
   ```ini
   [Unit]
   Description=Sim-aaS Node
   After=network.target

   [Service]
   Type=simple
   User=simaas
   WorkingDirectory=/opt/simaas
   ExecStart=/opt/simaas/venv/bin/simaas-node run
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

3. **Load Balancing**: Use nginx or similar for REST API load balancing
4. **Monitoring**: Implement health checks and monitoring
5. **Backup**: Regular backup of keystores and datastores

### Security Considerations

1. **Network Security**: Use firewalls to restrict access
2. **TLS**: Enable HTTPS for REST API
3. **Authentication**: Require authentication for all operations
4. **Authorization**: Implement proper access controls
5. **Audit Logging**: Log all security-relevant events

## Conclusion

The Sim-aaS Middleware provides a comprehensive platform for distributed simulation execution. This documentation covers the core architecture, components, APIs, and development guidelines. For more detailed information, refer to the individual module documentation and examples.

### Getting Help

- **Documentation**: Check the `docs/` directory for detailed usage guides
- **Examples**: Review the `examples/` directory for implementation examples
- **Issues**: Report bugs and feature requests on GitHub
- **Community**: Join the community discussions and forums

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

Follow the development guidelines and ensure all tests pass before submitting changes. 