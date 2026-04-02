# Sim-aaS Middleware

**Turn computational models into managed, interoperable simulation services.**

Sim-aaS (Simulation-as-a-Service) Middleware is a runtime infrastructure for containerised simulation models. It handles the full lifecycle: packaging models as self-describing processors, managing input/output data with provenance tracking, executing jobs in Docker containers, and enabling models to interact at runtime.

## What Makes This Different

Workflow engines typically chain isolated tasks: step A finishes, step B starts. Job schedulers run your code on a cluster but know nothing about what it does. Sim-aaS occupies a different space:

- **Runtime co-simulation.** Processors submitted together as a batch can discover each other and communicate directly via TCP sockets during execution. A room model and a thermostat controller run simultaneously, exchanging temperature readings and heater commands each timestep, not passing files between sequential stages.

- **Dynamic child job spawning.** A running processor can discover other deployed processors, submit sub-jobs, monitor their progress, and retrieve their results, all through the Namespace runtime API. Workflows don't need to be fully defined upfront; a processor can decompose its problem at runtime based on the data it sees.

- **Self-describing processors with provenance.** Every processor declares its inputs and outputs with types, formats, and optional JSON schemas. Every output produced automatically records which processor (down to the Git commit) and which inputs (by content hash) were used to produce it. Reproducibility tracking is automatic, not a separate step.

- **Cryptographic identity.** No passwords, no shared secrets, no central identity provider. Each participant has a self-sovereign identity backed by a private key. Every API request is cryptographically signed and verified. Trust is established through public key exchange, not by sharing credentials.

- **Pluggable backends.** The Data Object Repository (DOR) and Runtime Infrastructure (RTI) are plugins. Run locally with filesystem storage and Docker today; swap in cloud backends tomorrow, without changing a single line of processor code.

## Quick Overview

A typical workflow looks like this:

1. **Write a processor:** a Python class with a `run()` method, a `descriptor.json` declaring its I/O interface, and a `Dockerfile`.
2. **Start a node:** a Sim-aaS node provides data storage (DOR), job execution (RTI), and a REST API.
3. **Upload data:** add input data objects to the DOR with type and format metadata.
4. **Build and deploy:** build a Processor Docker Image (PDI), upload it to the DOR, and deploy it on the RTI.
5. **Submit a job:** reference the deployed processor and input data objects; the middleware handles the rest.
6. **Retrieve results:** outputs appear in the DOR with full provenance linking them back to their inputs and the exact processor version.

## Prerequisites

- Python 3.13+
- Docker
- Linux or macOS

## Installation

```bash
git clone https://github.com/simaas/sim-aas-middleware.git
cd sim-aas-middleware
python -m venv .venv
source .venv/bin/activate
pip install .
```

For development (includes test dependencies):

```bash
pip install ".[dev]"
```

## Quick Start

See [Getting Started](docs/01_getting_started.md) for a complete walkthrough using the `abc` example processor.

## Examples

| Example | What it demonstrates | Location |
|---------|---------------------|----------|
| **abc** | Basic input/output, secrets, progress reporting, cancellation | `examples/simple/abc/` |
| **ping** | Network diagnostics processor | `examples/simple/ping/` |
| **defg** | Optional inputs and outputs | `examples/simple/defg/` |
| **cosim** | Runtime co-simulation: room model and thermostat controller communicating via TCP sockets during execution | `examples/cosim/` |
| **prime** | Dynamic child job spawning: factorisation processor discovers and orchestrates factor-search sub-jobs at runtime | `examples/prime/` |

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/01_getting_started.md) | End-to-end walkthrough from installation to your first job |
| [Concepts](docs/02_concepts.md) | Core concepts: processors, data objects, identities, namespaces, provenance |
| [Writing a Processor](docs/03_writing_a_processor.md) | How to implement a processor: descriptor, Python code, Dockerfile |
| [Managing Data](docs/04_managing_data.md) | Adding, searching, downloading, and controlling access to data objects |
| [Running Jobs](docs/05_running_jobs.md) | Building images, deploying processors, submitting and monitoring jobs |
| [Identities and Security](docs/06_identities_and_security.md) | Keystores, identities, credentials, and the security model |
| [Plugin Development](docs/07_plugin_development.md) | Creating custom DOR and RTI backends |
| [Architecture](docs/08_architecture.md) | System design, modules, and communication layers |
| [Testing](docs/09_testing.md) | Test setup, structure, and running the test suite |

## License

MIT
