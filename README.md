# Simulation-as-a-Service (Sim-aaS) Middleware
The Sim-aaS Middleware provides the infrastructure to facilitate deployment and operations
of federations of computational models.

## Prerequisites
- Python 3.12
- Linux or MacOS operating system (not tested with Windows)

## Install

Clone the repository:
```shell
git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware
```

Create and activate the virtual environment:
```shell
python3.12 -m venv venv
source venv/bin/activate
```

Install the Sim-aaS Middleware:
```shell
pip install ./sim-aas-middleware
```

Once done, you may deactivate the virtual environment - or keep it activated if you want
to starting using the Sim-aaS Middleware:
```shell
deactivate
```

## Usage
The Sim-aaS Middleware can be used via a Command Line Interface (CLI) with this command: 
```shell
simaas-cli
```

The CLI can be used in a non-interactive manner by providing corresponding command line 
arguments. In addition, commands also allow interactive use of the CLI in which case the 
user is prompted for input. The following sections explain how to use the CLI for common 
use-cases.

> If you are new to using the Sim-aaS Middleware, it is recommended you work through 
> each of the following topics in sequence. It should give you a basic understanding
> of how to use the various commands.

- [Manage Identities](docs/usage_manage_identities.md)
- [Running a Sim-aaS Node Instance](docs/usage_run_simaas_node.md)
- [Adding and Removing a Data Object](docs/usage_add_remove_data_object.md)
- [Granting/Revoking Access to Data Objects](docs/usage_grant_revoke_access.md)
- [Building a Processor Docker Image](docs/usage_build_processor_docker_image.md)
- [Deploying/Undeploying of Processors](docs/usage_deploy_undeploy_processors.md)
- [Job Submission and Monitoring](docs/usage_job_submission_and_monitoring.md)


## Processors and Processor Adapters
In the Sim-aaS Middleware framework, **processors** and **processor adapters** serve as the 
standardized execution interface for computational models and external applications, enabling 
them to run as jobs within a containerized, managed runtime environment. Both are used 
exactly the same way but differ semantically.

### Processor vs Processor Adapter: What's the Difference?

| Feature                    | **Processor**                                          | **Processor Adapter**                                     |
|----------------------------|--------------------------------------------------------|-----------------------------------------------------------|
| **Implements Logic?**      | Yes – contains the full business logic or simulation   | No – delegates to a third-party tool or executable        |
| **Dependencies?**          | Internal Python code and libraries                     | External application (e.g., compiled binary, model suite) |
| **Example Use Case**       | Pure Python climate model                              | Wrapper for external tool like WRF, MATLAB, or others     |
| **Code Ownership**         | Owned and maintained in-house                          | External application, wrapped via adapter logic           |
| **Complexity**             | Simple to complex logic in Python                      | Primarily setup, I/O handling, and CLI interfacing        |

> Both types must expose a common interface to the RTI through a descriptor and standard file layout, but their internal purpose is distinct.
> For simplicity, for the remainder of this documentation, we will use the term processor to refer to both, processors and processor adapters.

### Directory Structure
Every processor or adapter must follow a standardized folder layout, regardless of whether 
it implements custom logic or wraps an external program.
```
processor/
├── descriptor.json # Interface contract: input/output schema
├── processor.py # Execution logic or application wrapper
└── Dockerfile # Self-contained runtime environment
```
> 💡 See the examples at `/examples/...` in this repository.

### Related Topics
Learn more about related topics:
- [Processor Descriptors (descriptor.json)](docs/processor_descriptor.md)
- [Processor/Adapter Implementation (processor.py)](docs/processor_implementation.md)
- [Processor Dockerfile](docs/processor_dockerfile.md)
