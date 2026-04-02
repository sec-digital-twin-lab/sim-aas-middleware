# Architecture

This document describes the system design of Sim-aaS Middleware for developers who want to understand or contribute to the codebase.

## Layered Architecture

The middleware is organized into four layers:

```
┌─────────────────────────────────────────────────┐
│  CLI Layer                                      │
│  simaas/cli/                                    │
│  Commands, prompts, output formatting           │
├─────────────────────────────────────────────────┤
│  Service Layer                                  │
│  simaas/dor/  simaas/rti/  simaas/nodedb/      │
│  simaas/namespace/  simaas/node/  simaas/plugins│
│  Business logic, state management, plugins      │
├─────────────────────────────────────────────────┤
│  Communication Layer                            │
│  simaas/rest/  simaas/p2p/                      │
│  FastAPI + Auth (REST), ZeroMQ + Curve (P2P)    │
├─────────────────────────────────────────────────┤
│  Core Layer                                     │
│  simaas/core/                                   │
│  Identity, keystore, processor base, helpers    │
└─────────────────────────────────────────────────┘
```

### CLI Layer (`simaas/cli/`)

The user-facing command-line interface. Built on `argparse` with a custom `CLIParser`/`CLICommand`/`CLICommandGroup` framework. The CLI communicates with the service layer via REST API calls (using the `proxy` module).

Key files:
- `saas_cli.py`: entry point, command registration
- `cmd_service.py`: `service` command (starts a node)
- `cmd_identity.py`: identity/keystore management
- `cmd_image.py`: PDI build/export/import
- `cmd_dor.py`: data object operations
- `cmd_rti.py`: processor and job management
- `cmd_job_runner.py`: the job runner that executes inside containers (exposed as `simaas-cli run`, not user-facing)
- `helpers/`: shared utilities (prompts, output formatting, address handling, etc.)

### Service Layer

The core business logic, organized by domain:

**`simaas/nodedb/`**: identity and peer registry. Backed by SQLite via SQLAlchemy. Stores the node's own identity, known peers, known identities, and namespace information.

**`simaas/dor/`**: data object repository interface. Defines the API contract (`DORInterface`) and schemas. Actual storage is delegated to plugins.

**`simaas/rti/`**: runtime infrastructure. `RTIServiceBase` in `base.py` handles the common logic: job state management, input validation, async worker coordination, database persistence. Actual container management is delegated to plugins.

**`simaas/namespace/`**: the runtime API available to processors. `DefaultNamespace` proxies operations to the custodian node via P2P. `SyncNamespace` (in `sync.py`) provides synchronous wrappers for use in processor threads.

**`simaas/node/`**: the node abstraction. `DefaultNode` composes all services (NodeDB, DOR, RTI, P2P, REST) and manages their lifecycle (startup/shutdown).

**`simaas/plugins/`**: plugin discovery and built-in plugins (`dor_fs`, `rti_docker`, `rti_aws`).

### Communication Layer

**`simaas/rest/`**: HTTP API built on FastAPI + Uvicorn. Runs in a daemon thread. Endpoints are registered dynamically from each enabled service via `EndpointDefinition` objects. Authentication uses per-request cryptographic signatures (see [Identities and Security](06_identities_and_security.md)).

**`simaas/p2p/`**: encrypted peer-to-peer messaging built on ZeroMQ (ROUTER/DEALER pattern) with CurveZMQ encryption. Runs in a background thread with its own event loop. Messages are dispatched to registered `P2PProtocol` handlers.

### Core Layer (`simaas/core/`)

Foundational abstractions used throughout the codebase:

- `identity.py` / `keystore.py`: cryptographic identity and key management
- `eckeypair.py` / `rsakeypair.py` / `keypair.py`: key pair abstractions
- `processor.py`: `ProcessorBase` abstract class
- `schemas.py`: shared Pydantic models
- `errors.py`: exception hierarchy
- `logging.py`: logging configuration with ID shortening and sensitive data redaction
- `helpers.py`: utility functions
- `async_helpers.py`: async/sync bridge utilities

## Communication Patterns

### REST (External)

The CLI and external clients communicate with nodes via REST. All endpoints are under `/api/v1/` with sub-paths:

- `/api/v1/db/`: NodeDB (identities, peers, namespaces)
- `/api/v1/dor/`: Data Object Repository
- `/api/v1/rti/`: Runtime Infrastructure

Authentication: custom `saasauth-iid` and `saasauth-signature` headers on every request. The signature covers `METHOD:URL` + canonical JSON body, signed with the caller's EC private key.

Authorization: enforced via decorators on interface methods (`@requires_ownership`, `@requires_authentication`, `@requires_access`, etc.), resolved at registration time into FastAPI dependency injection.

### P2P (Internal)

Nodes communicate with each other and with job runner containers via P2P. The P2P layer handles:

**Network management:**
- `P2PJoinNetwork` / `P2PLeaveNetwork`: peer membership
- `P2PUpdateIdentity` / `P2PGetIdentity` / `P2PGetNetwork`: identity propagation and peer discovery

**Data transfer:**
- `P2PLookupDataObject`: find which node has a data object
- `P2PFetchDataObject`: download content (supports chunked transfer for large files, 1MB chunks)
- `P2PPushDataObject`: upload content to a DOR

**Job execution:**
- `P2PRunnerPerformHandshake`: job runner registers with custodian, receives job details and secrets
- `P2PPushJobStatus`: job runner reports status updates to custodian
- `P2PInterruptJob`: custodian sends cancellation signal to runner

**Namespace:**
- `P2PNamespaceServiceCall`: processors call DOR/RTI operations through the namespace, proxied via P2P to the custodian

**Diagnostics:**
- `P2PLatency` / `P2PThroughput`: network performance measurement

All P2P communication is encrypted via CurveZMQ using keys derived from each participant's signing key.

## Data Flow: Job Execution

Here's the complete data flow when a job is submitted and executed:

```
User (CLI)                    Node (REST + Services)           Container (Job Runner)
    │                                │                                │
    │──POST /api/v1/rti/job────────>│                                │
    │  (signed request)              │                                │
    │                                │──validate inputs               │
    │                                │──create container──────────────>│
    │                                │                                │
    │                                │<──P2P handshake────────────────│
    │                                │──send job + secrets───────────>│
    │                                │                                │
    │                                │                                │──fetch inputs (P2P)
    │                                │<──P2PFetchDataObject───────────│
    │                                │──send content─────────────────>│
    │                                │                                │
    │                                │                                │──run processor
    │                                │                                │
    │                                │<──P2PPushJobStatus─────────────│
    │                                │  (periodic status updates)     │
    │                                │                                │
    │                                │                                │──push outputs (P2P)
    │                                │<──P2PPushDataObject────────────│
    │                                │──store in DOR                  │
    │                                │                                │
    │──GET /api/v1/rti/job/status──>│                                │
    │<──{state: successful, ...}─────│                                │
```

## Async Architecture

The middleware is fully async-native. Key patterns:

- **Service layer**: all DOR, RTI, NodeDB, and Namespace operations are async
- **P2P service**: runs in a background thread with its own event loop; dispatches protocol handlers as concurrent `asyncio.create_task` calls
- **REST service**: runs in a daemon thread via Uvicorn; FastAPI handles async endpoints natively
- **Processor threads**: processors run in sync threads; `SyncNamespace` bridges async APIs using thread-local event loops
- **CLI entry points**: `asyncio.run()` is only called at the top level in CLI commands and test fixtures

The `async_helpers.py` module provides `run_coro_safely()` for bridging sync code to async code, handling the case where an event loop may or may not already be running.

## Database

SQLAlchemy with SQLite is used for local state persistence:

- **NodeDB**: peer registry, identity store, namespace budgets/reservations
- **DOR** (`dor_fs` plugin): data object metadata, tags, access lists, provenance recipes
- **RTI**: deployed processor registry, job state and history

Each uses its own SQLite database file in the node's datastore directory.

## Key Design Decisions

**Plugin architecture for DOR and RTI.** Storage and execution are the most likely components to vary across deployments. Making them plugins allows the core middleware to remain stable while backends can be swapped.

**Dual communication layers.** REST for external clients (familiar, debuggable, stateless) and P2P for inter-node and container communication (encrypted, efficient for large transfers, doesn't require HTTP infrastructure inside containers).

**Content-addressed storage.** Data objects identified by content hash enables deduplication, immutable provenance references, and simple integrity verification.

**Decorator-based authorization.** Security annotations on abstract interface methods propagate to concrete REST endpoints automatically, keeping security declarations close to the business logic they protect.

**Processor-as-container.** Packaging processors as Docker images provides isolation, reproducibility, and portability. The job runner inside each container handles all middleware integration, so processors only implement business logic.
