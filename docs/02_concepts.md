# Concepts

This document explains the core abstractions in Sim-aaS and how they work together. Understanding these concepts makes the rest of the documentation easier to follow.

## Processors

A **processor** is a containerised computational model with a formally declared interface. It is the central abstraction in Sim-aaS: the unit of computation that gets deployed, executed, and composed.

A processor consists of three files:

- **`descriptor.json`**: declares the processor's name, inputs, outputs (with types and formats), and any required secrets. This is the processor's contract with the outside world.
- **`processor.py`**: a Python class extending `ProcessorBase` that implements a `run()` method. This is where the actual computation happens.
- **`Dockerfile`**: packages everything into a Docker image (the Processor Docker Image, or PDI) that the middleware can execute.

The middleware relies on the descriptor. Because every processor formally declares what it consumes and produces, the middleware can validate inputs at submission time, verify outputs after execution, and track provenance automatically. The processor itself never needs to know where its inputs come from or where its outputs go; it just reads files from a working directory and writes files back.

### Why This Matters

Without a formal interface, running models typically involves manual file management and informal conventions about inputs and outputs. The descriptor replaces that: it declares the interface, the middleware enforces it, and provenance is recorded automatically. A processor can be deployed on any node running the middleware, composed with other processors, and its outputs are traceable back to their origins.

## Data Object Repository (DOR)

The **DOR** is where all data lives: input files, output results, and processor images. Every piece of data in the DOR is a **data object** with:

- **Content**: the actual file (any format: JSON, CSV, binary, a Docker image tar, etc.)
- **Content hash** (`c_hash`): a SHA-256 hash of the content, computed on ingest. This makes data objects content-addressed: the same content always produces the same hash.
- **Type and format**: metadata declaring what the data represents (e.g., `data_type: "JSONObject"`, `data_format: "json"`). Used for matching inputs to processor descriptors.
- **Owner**: the identity that owns the data object. Only the owner can delete it, manage access, or transfer ownership.
- **Access control**: data objects can be unrestricted (anyone can read) or restricted to a specific access list.
- **Tags**: arbitrary key-value metadata for organization and search.
- **License**: Creative Commons-style flags (BY, SA, NC, ND) for declaring usage terms.
- **Provenance**: if the data object was produced by a processor, it carries a recipe recording which processor and inputs created it.

The DOR is implemented as a plugin. The built-in `dor_fs` plugin uses SQLite for metadata and the local filesystem for content storage.

## Runtime Infrastructure (RTI)

The **RTI** handles processor deployment and job execution. Its responsibilities:

- **Deploy** a processor: load its Docker image and register it as available for jobs.
- **Submit** a job: validate inputs against the processor's descriptor, create a Docker container, and start execution.
- **Monitor** jobs: track state (initialised, running, successful, failed, cancelled), progress percentage, and messages from the processor.
- **Manage** the lifecycle: cancel running jobs, clean up completed ones, undeploy processors.

The RTI is also a plugin. The built-in `rti_docker` plugin runs processor containers locally using Docker. An `rti_aws` plugin is available for running on AWS Batch.

### Job Lifecycle

```
submit -> initialised -> running -> successful
                                 -> failed
                                 -> cancelled
```

When a job is submitted, the RTI:

1. Validates that all required inputs are provided and their types match the processor's descriptor
2. Creates a Docker container from the processor's image
3. The job runner inside the container fetches input data, runs the processor, and pushes outputs to the DOR
4. Status updates flow back to the RTI throughout execution

## Identities

Sim-aaS uses **cryptographic identities** instead of usernames and passwords. Each identity is a key pair:

- **Private key**: stays on the owner's machine, encrypted in a keystore file. Used to sign requests.
- **Public key**: shared with the network. Used by others to verify signatures.

Every REST API request includes the caller's identity ID and a cryptographic signature over the request content. The receiving node verifies the signature against the identity's public key. This means:

- No passwords to leak or manage
- No central identity provider to maintain or secure
- Trust is established by exchanging public keys, not by sharing secrets
- Each request is independently verifiable

The keystore also stores SSH and GitHub credentials for cloning processor repositories during image builds.

## Namespace

The **Namespace** is the runtime API available to processors during execution. When a processor's `run()` method is called, it receives a `namespace` parameter that provides access to:

- **RTI operations**: submit child jobs, check job status, discover deployed processors, cancel jobs
- **DOR operations**: fetch data objects, search, add new data, manage access
- **Identity**: access the keystore for signing and encryption

The Namespace enables two additional capabilities:

### Runtime Co-Simulation

When multiple tasks are submitted together as a **batch**, they execute simultaneously and can discover each other through the Namespace. Each batch member knows the others' network addresses and exposed ports, enabling direct communication during execution.

For example, in the `cosim` example:
- A room simulator opens a TCP server socket on port 7001
- A thermostat controller queries `namespace.rti.get_batch_status()` to discover the room's address
- They communicate directly via TCP throughout the simulation, exchanging temperature data and heater commands every timestep

This differs from workflow engines where tasks run in isolation and communicate only by passing files between stages.

### Dynamic Child Job Spawning

A running processor can use the Namespace to orchestrate sub-computations:
- Discover deployed processors via `namespace.rti.get_all_procs()`
- Submit child jobs via `namespace.rti.submit()`
- Monitor progress via `namespace.rti.get_job_status()`
- Retrieve results via `namespace.dor.get_content()`

This means workflows don't need to be fully defined upfront. A processor can inspect its input data at runtime and decide how to decompose the problem, splitting work across multiple sub-jobs, choosing different processors based on data characteristics, or adapting its strategy based on intermediate results.

The `prime` example demonstrates this: a factorisation processor discovers available factor-search processors, submits sub-jobs for different search ranges, and collects the results.

## Provenance

Every output data object produced by a processor automatically receives a **provenance record** (called a recipe) containing:

- **Processor identity**: the Git repository URL, commit ID, path within the repo, and the full processor descriptor
- **Inputs consumed**: each input's content hash, data type, and format
- **Output produced**: the output's content hash, data type, and format

Because inputs are referenced by content hash (not file name or object ID), provenance is immutable and verifiable. You can trace any output back through the chain of processors and inputs that produced it, regardless of when or where the computation ran.

Provenance graphs can span multiple processing steps. If processor A produces output X, and processor B consumes X to produce Y, querying the provenance of Y returns the complete chain: B consumed X (produced by A from its inputs).

## Processor Docker Images (PDIs)

A **PDI** is a Docker image built from a processor directory. It contains:

- The processor's code (`processor.py`, `descriptor.json`, and any dependencies)
- The Sim-aaS middleware (specifically the job runner)
- A Python virtual environment with all installed packages

PDIs are stored in the DOR as regular data objects, which means they benefit from the same content addressing, access control, and provenance tracking as any other data. When a processor is deployed on an RTI, the RTI fetches the PDI from the DOR and loads it into Docker.

The image name follows the pattern `{processor-name}:{content-hash}`, making images content-addressable. Rebuilding from the same source produces the same hash.

## Putting It All Together

Here's how these concepts interact in a typical job execution:

1. A user **creates an identity** (keystore with signing keys)
2. They **start a node** with DOR and RTI plugins enabled
3. They **write a processor** (descriptor + Python code + Dockerfile) and build a PDI
4. The PDI is **uploaded to the DOR** as a data object
5. Input data is **added to the DOR** with appropriate types and formats
6. The processor is **deployed on the RTI** by referencing the PDI's object ID
7. A **job is submitted** referencing the processor and input data objects
8. The RTI **creates a container** from the PDI and starts the job runner
9. The job runner **fetches inputs** from the DOR, **runs the processor**, and **pushes outputs** back to the DOR
10. Each output gets a **provenance record** linking it to the processor version and input hashes
11. The user **retrieves results** from the DOR, with provenance linking back to inputs and processor version
