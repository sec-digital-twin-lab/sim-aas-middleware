# Architecture Overview

The **Simulation-as-a-Service (Sim-aaS) Middleware** is a distributed computational platform designed to enable secure, scalable execution of simulation models and computational workloads across a network of heterogeneous nodes.

## Primary Use Cases

1. **Distributed Scientific Computing**: Execute complex simulations across multiple nodes with automatic load balancing and resource management
2. **Federated Simulation**: Coordinate real-time co-simulations between different computational models
3. **Computational Model Integration**: Standardize interfaces for legacy and new computational models through containerized adapters
4. **Secure Data Processing**: Process sensitive data with cryptographic access controls and end-to-end encryption
5. **Multi-Cloud Orchestration**: Execute workloads across local infrastructure and cloud platforms (AWS, with extensibility for others)

## Design Principles

### Security-First Architecture
- **Cryptographic Identity**: All operations are tied to verifiable cryptographic identities using EC/RSA key pairs
- **Zero-Trust Networking**: Every communication is authenticated and optionally encrypted using ZeroMQ Curve protocol
- **End-to-End Data Protection**: Data objects can be encrypted at rest and in transit with user-controlled keys
- **Fine-Grained Access Control**: Data access is controlled at the individual object level with explicit permission grants

### Decentralized by Design
- **No Central Authority**: The system operates as a peer-to-peer network without single points of failure
- **Distributed Storage**: Data objects are replicated across network nodes for availability and resilience
- **Dynamic Discovery**: Nodes discover each other through a distributed boot node mechanism
- **Autonomous Operation**: Nodes can operate independently and rejoin the network seamlessly

### Container-First Execution Model
- **Isolation**: All computational workloads execute in Docker containers for security and reproducibility
- **Standardization**: Processors follow a common interface contract enabling automated orchestration
- **Portability**: Support for local Docker execution and cloud platforms with minimal configuration changes
- **Resource Management**: Container-based resource allocation with CPU and memory budgeting

### Self-Describing Components
- **Interface Contracts**: Processors define formal input/output specifications in `descriptor.json` files
- **Data Type System**: Rich metadata system for semantic data compatibility checking
- **Provenance Tracking**: Complete lineage tracking for all computational results and data transformations
- **Version Control Integration**: Built-in support for Git-based processor versioning and reproducibility

## System Architecture

The Sim-aaS Middleware follows a **service-oriented, microservices architecture** where each node in the network hosts a collection of specialized services that communicate through well-defined interfaces.

### Layered Architecture

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
│  ┌─────────────────────┐ ┌─────────────────────────────────┐│
│  │     P2P Network     │ │      HTTP/REST API              ││
│  │  (ZeroMQ + Curve)   │ │    (FastAPI + Auth)             ││
│  └─────────────────────┘ └─────────────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│                    Core Layer                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌─────────────────┐│
│  │Identity │ │Keystore │ │ Processor   │ │   Namespaces    ││
│  │Managemnt│ │Security │ │ Framework   │ │ & Resources     ││
│  └─────────┘ └─────────┘ └─────────────┘ └─────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### Network Topology

The system operates as a **structured peer-to-peer network** where:
- **Boot Nodes**: Special nodes that facilitate network joining and initial peer discovery
- **Full Nodes**: Provide both storage (DOR) and execution (RTI) services
- **Specialized Nodes**: Can run storage-only or execution-only configurations
- **Client Nodes**: Lightweight nodes that consume services without hosting them

### Data Flow

```
User Input → CLI → REST API → Service Layer → P2P Network → Remote Nodes
     ↓                                      ↓
Data Objects ← DOR ← Database ← Processing Results ← RTI ← Processors
```

## Core Services

| Service | Purpose | Location |
|---------|---------|----------|
| **DOR** | Data Object Repository - distributed storage with provenance | `simaas/dor/` |
| **RTI** | Runtime Infrastructure - job execution and processor management | `simaas/rti/` |
| **NodeDB** | Network registry and service discovery | `simaas/nodedb/` |
| **P2P** | Secure peer-to-peer communication | `simaas/p2p/` |
| **REST** | HTTP API for external integration | `simaas/rest/` |
| **CLI** | Command-line interface | `simaas/cli/` |
| **Core** | Identity, keystore, processor framework | `simaas/core/` |

For detailed component documentation, see [Component Reference](dev_components.md).
