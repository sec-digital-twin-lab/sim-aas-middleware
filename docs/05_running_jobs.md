# Running Jobs

This guide covers the full job execution workflow: building processor images, deploying processors, submitting jobs, and monitoring execution.

All commands assume a running Sim-aaS node. See [Getting Started](01_getting_started.md) for setup instructions.

## Building Processor Docker Images

Before a processor can be deployed, it must be packaged as a Processor Docker Image (PDI) and uploaded to the DOR. These are two separate steps: build produces a `.pdi` file locally, import uploads it to the DOR.

### Build from Local Source

Navigate to the processor directory and run:

```bash
simaas-cli image build-local .
```

Or specify the platform explicitly (recommended when targeting different architectures):

```bash
simaas-cli image build-local --arch linux/amd64 .
```

The command:
1. Builds a Docker image from the processor's Dockerfile
2. Creates a `gpp.json` file recording the Git repository, commit ID, and processor path
3. Exports the image as a `.pdi` file (a tar archive with appended metadata)

The `SIMAAS_REPO_PATH` environment variable must point to the middleware repository root (or you will be prompted for it). The build command does not interact with any node.

### Build from GitHub

Build directly from a Git repository:

```bash
simaas-cli image build-github \
    --repository https://github.com/org/repo.git \
    --commit-id abc123 \
    --proc-path path/to/processor \
    output.pdi
```

This clones the repo, checks out the specified commit, and builds the PDI. GitHub credentials can be passed via `--git-username`/`--git-token` or via `GITHUB_USERNAME`/`GITHUB_TOKEN` environment variables.

### Import and Export

Import a PDI file into the DOR (prompts for node address and keystore):

```bash
simaas-cli image import <path-to-pdi-file>
```

The output includes the DOR object ID of the uploaded image. This is the `proc_id` you use for deployment.

Export a PDI from the DOR to a local file:

```bash
simaas-cli image export --obj-id <proc-obj-id> <destination-path>
```

### Build Options

| Option | Description |
|--------|-------------|
| `--arch PLATFORM` | Target platform (e.g., `linux/amd64`, `linux/arm64`). |
| `--target STAGE` | Dockerfile build target (e.g., `test` for the test stage). |
| `--force-build` | Rebuild even if a matching image already exists. |
| `--delete-docker-image` | Remove the Docker image after export (saves disk space). |
| `--simaas-repo-path PATH` | Path to the middleware repo. Can also be set via `SIMAAS_REPO_PATH` environment variable. |
| `--verbose` | Show Docker build output. |

## Deploying Processors

Deploy a processor on the RTI so it can accept jobs:

```bash
simaas-cli rti proc deploy
```

The interactive prompt lists available processor images from the DOR. Select the one you want to deploy. You can also specify it directly:

```bash
simaas-cli rti proc deploy --proc-id <proc-obj-id>
```

### Volumes

Processors can mount host volumes for accessing shared data or scratch space:

First, create a volume reference:

```bash
simaas-cli rti volume create fs --name my-data --path /path/to/host/dir
```

Then attach it when deploying:

```bash
simaas-cli rti proc deploy --proc-id <proc-obj-id> my-data:/data:true
```

Volume format: `name:mount_path:read_only` (e.g., `my-data:/mnt/storage:true` or `my-data:/mnt/storage:false`).

For AWS deployments, use EFS volumes:

```bash
simaas-cli rti volume create efs --name shared --efs-fs-id fs-abc123
```

### Listing Deployed Processors

```bash
simaas-cli rti proc list
```

Shows all deployed processors with their state, name, image info, and I/O types. Add `--json` for machine-readable output.

### Viewing Processor Details

```bash
simaas-cli rti proc show --proc-id <proc-obj-id>
```

### Undeploying

```bash
simaas-cli rti proc undeploy --proc-id <proc-obj-id>
```

A processor can only be undeployed when it has no active jobs. Use `--force` to override.

## Submitting Jobs

### Interactive Submission

```bash
simaas-cli rti job submit
```

The interactive prompt walks through:

1. **Select a processor** from the list of deployed processors
2. **Specify inputs**: for each declared input, choose:
   - **by-reference**: select a data object from the DOR (the standard approach)
   - **by-value**: provide inline JSON content (useful for parameters)
3. **Configure outputs**: for each declared output:
   - Owner identity
   - Destination node (where the output should be stored)
   - Whether to restrict access
   - Whether to encrypt content
4. **Name the task** (optional)
5. **Select a resource budget**: CPU and memory limits for the container
6. **Add another task?** Submit multiple tasks as a batch for co-simulation

### Submission from File

For automation, create task descriptor JSON files and submit them:

```bash
simaas-cli rti job submit task1.json task2.json
```

### By-Reference vs By-Value Inputs

**By-reference** inputs point to existing data objects in the DOR. The job runner fetches the content from the DOR before execution. This is the standard approach for persistent data.

**By-value** inputs embed JSON content directly in the task. The job runner writes this content to a file in the working directory. This is useful for dynamically generated parameters, especially from child job spawning (see [Writing a Processor](03_writing_a_processor.md#dynamic-child-job-spawning-pattern)).

### Batch Submission (Co-Simulation)

When you submit multiple tasks together, they form a **batch**:

- All tasks in a batch start simultaneously
- A synchronisation barrier ensures all containers are ready before any processor begins execution
- Each batch member can discover the others' network addresses and exposed ports via `namespace.rti.get_batch_status()`

This is how co-simulation works in Sim-aaS. See the [cosim example](../examples/cosim/) for a complete walkthrough.

### Resource Budgets

Each task can specify CPU and memory limits:

| Resource | Description |
|----------|-------------|
| vCPUs | Number of virtual CPUs allocated to the container |
| Memory | RAM in MB allocated to the container |

The Docker RTI translates these to Docker container resource constraints (`cpu_quota`, `mem_limit`).

### Namespaces

Tasks can specify a namespace for resource accounting. Namespaces have resource budgets (total vCPUs and memory) that limit how many jobs can run simultaneously:

```bash
simaas-cli namespace update --name my-project --vcpus 8 --memory 16384
```

When a job is submitted with a namespace, the middleware reserves resources from the namespace budget. When the job completes, the resources are released.

## Monitoring Jobs

### Job Status

```bash
simaas-cli rti job status <job-id>
```

Returns:
- **state**: `uninitialised`, `initialised`, `running`, `successful`, `failed`, `cancelled`
- **progress**: 0-100 percentage
- **output**: map of output names to data object metadata (when successful)
- **errors**: list of errors (when failed)
- **message**: latest message from the processor

### Job Details

```bash
simaas-cli rti job inspect <job-id>
```

Shows detailed job information including task details, timestamps, and custodian info. Add `--json` for full output.

### Job Logs

```bash
simaas-cli rti job logs <job-id>
```

Shows progress messages sent by the processor during execution, the messages from `listener.on_message()`.

### Listing Jobs

```bash
simaas-cli rti job list                # active jobs
simaas-cli rti job list --period 1d    # jobs from the last day
simaas-cli rti job list --period 2h    # jobs from the last 2 hours
```

## Cancelling Jobs

Cancel a running job:

```bash
simaas-cli rti job cancel <job-id>
```

This sends an interrupt signal to the processor. The processor's `interrupt()` method is called, and the job transitions to `cancelled` state.

To forcefully remove a job regardless of state:

```bash
simaas-cli rti job cancel <job-id> --purge
```

## Job Execution Details

When a job is submitted, here's what happens inside the middleware:

1. **Validation**: the RTI checks that all required inputs are provided, their types match the processor's descriptor, and resource budgets are available
2. **Container creation**: the Docker RTI creates a container from the PDI, mapping ports and mounting volumes
3. **P2P handshake**: the job runner inside the container establishes an encrypted P2P connection with the custodian node
4. **Input fetch**: by-reference inputs are fetched from the DOR; by-value inputs are written as files
5. **Input verification**: data types and formats are verified against the descriptor; JSON schemas are validated if declared
6. **Batch synchronisation**: if part of a batch, the runner waits until all batch members are ready
7. **Processor execution**: the processor's `run()` method is called
8. **Output push**: each output is pushed to the DOR with a provenance record
9. **Status reporting**: status updates flow back to the custodian throughout execution

## Building Pipelines

A pipeline chains multiple adapters so that the output of one becomes the input of the next. The middleware does not have a built-in pipeline orchestrator — you compose pipelines by submitting jobs sequentially and wiring outputs to inputs through their DOR object IDs.

### Designing Descriptors for Composability

The key to composable adapters is matching `data_type` / `data_format` pairs across descriptors. When adapter A produces an output and adapter B consumes it, both must agree on the type and format.

Consider a two-stage pipeline where a preprocessing adapter produces a run package, and a simulation adapter consumes it:

**Preprocessor descriptor** (`prep/descriptor.json`):
```json
{
  "name": "proc-sim-prep",
  "input": [
    {"name": "parameters", "data_type": "MyProject.PrepParameters", "data_format": "json"},
    {"name": "geometries", "data_type": "MyProject.GeoVectorData", "data_format": "geojson"}
  ],
  "output": [
    {"name": "run_package", "data_type": "MyProject.RunPackage", "data_format": "tar.gz"}
  ],
  "required_secrets": []
}
```

**Simulation descriptor** (`sim/descriptor.json`):
```json
{
  "name": "proc-sim-run",
  "input": [
    {"name": "parameters", "data_type": "MyProject.SimParameters", "data_format": "json"},
    {"name": "run_package", "data_type": "MyProject.RunPackage", "data_format": "tar.gz"}
  ],
  "output": [
    {"name": "results", "data_type": "MyProject.SimResults", "data_format": "hdf5"}
  ],
  "required_secrets": []
}
```

The `MyProject.RunPackage` / `tar.gz` pair is the contract between the two stages. The middleware enforces the match at submission time.

### Running a Pipeline with the CLI

Once both adapters are built, imported, and deployed, run the pipeline step by step:

**Step 1 — Submit the preprocessing job:**

```bash
simaas-cli rti job submit
# Select proc-sim-prep
# Provide parameters (by-value JSON) and geometries (by-reference DOR object)
# → Returns job ID, e.g. job-001
```

**Step 2 — Wait for completion and note the output ID:**

```bash
simaas-cli rti job status job-001
# When state is "successful", the output section shows:
#   run_package: obj_id=abc123, c_hash=...
```

**Step 3 — Submit the simulation job using the output as input:**

```bash
simaas-cli rti job submit
# Select proc-sim-run
# For "parameters": provide by-value JSON
# For "run_package": select by-reference → enter obj_id abc123
# → Returns job ID, e.g. job-002
```

**Step 4 — Retrieve results:**

```bash
simaas-cli rti job status job-002
# When successful, download the results:
simaas-cli dor download <results-obj-id>
```

### Running a Pipeline with Task Files

For repeatable execution, define tasks as JSON files with the output IDs wired in:

**step1_task.json:**
```json
{
  "proc_id": "<prep-proc-obj-id>",
  "input": [
    {"name": "parameters", "type": "value", "value": {"name": "run-01", "bbox": [1.2, 103.6, 1.5, 104.0]}},
    {"name": "geometries", "type": "reference", "obj_id": "<geometries-obj-id>"}
  ],
  "output": [
    {"name": "run_package", "owner_iid": "<your-iid>", "restricted_access": false, "content_encrypted": false}
  ]
}
```

Submit and capture the output:

```bash
simaas-cli rti job submit step1_task.json
# Wait for completion, note the run_package obj_id
```

Then reference that output in the next step's task file and submit it.

### Suggestions for Larger Pipelines

The same pattern extends to any number of stages. Here are a few recommendations that may help when scaling beyond two or three adapters:

- **Consider defining data types upfront**: Listing every intermediate data type and format before building adapters can serve as the system's wiring diagram and catch integration mismatches early.
- **Namespaced types help avoid collisions**: Prefixes like `MyProject.RunPackage` make it clear which types belong to which system. See [Designing Data Contracts](04_managing_data.md#designing-data-contracts-for-multi-adapter-systems).
- **Separate parameters from data inputs**: Each adapter typically has its own `parameters` input (by-value JSON) alongside the data inputs that chain from previous stages. This keeps the parameterisation of each step independent.
- **Script the orchestration**: For pipelines with more than a few stages, a script that submits jobs sequentially, polls for completion, and wires output IDs to the next step's inputs avoids manual bookkeeping. See [Programmatic Access](#programmatic-access) below.

### Assembling a Multi-Adapter System

This section walks through the end-to-end process of setting up a system with multiple adapters. The individual steps are covered in detail earlier in this guide; this is the overall sequence.

**1. Build all PDIs**

Each adapter lives in its own directory with a `descriptor.json`, `processor.py`, and `Dockerfile`. Build them all:

```bash
cd adapters/prep && simaas-cli image build-local --arch linux/amd64 .
cd adapters/sim  && simaas-cli image build-local --arch linux/amd64 .
cd adapters/analysis && simaas-cli image build-local --arch linux/amd64 .
```

PDI builds are independent and can run in parallel.

**2. Start a node**

```bash
simaas-cli service --profile dev
```

**3. Import all PDIs**

```bash
simaas-cli image import adapters/prep/proc-sim-prep.pdi
simaas-cli image import adapters/sim/proc-sim-run.pdi
simaas-cli image import adapters/analysis/proc-analysis.pdi
```

Note the returned `obj_id` for each — these are the processor IDs you'll use for deployment.

**4. Deploy all processors**

```bash
simaas-cli rti proc deploy --proc-id <prep-obj-id>
simaas-cli rti proc deploy --proc-id <sim-obj-id>
simaas-cli rti proc deploy --proc-id <analysis-obj-id>
```

Verify with `simaas-cli rti proc list`.

**5. Upload shared input data**

```bash
simaas-cli dor add --data-type MyProject.GeoVectorData --data-format geojson --assume-creator geometries.geojson
```

**6. Run the pipeline**

Submit jobs sequentially, wiring each step's outputs to the next step's inputs as described in [Running a Pipeline with the CLI](#running-a-pipeline-with-the-cli). For systems with more than a few adapters, consider scripting this with the [Programmatic Access](#programmatic-access) API.

## Programmatic Access

The middleware exposes a REST API and Python proxy classes that allow scripting against a running node. This is essential for automating pipelines, building orchestration tools, or integrating with external systems.

### Python Proxy Classes

Three proxy classes mirror the three services:

```python
from simaas.dor.api import DORProxy
from simaas.rti.api import RTIProxy
from simaas.nodedb.api import NodeDBProxy

# Connect to a node at localhost:5100
dor = DORProxy(('localhost', 5100))
rti = RTIProxy(('localhost', 5100))
db = NodeDBProxy(('localhost', 5100))
```

The `remote_address` tuple is `(host, port)`. No authentication is needed for read operations; write operations require a `Keystore` for signing.

#### Loading a Keystore

Most write operations require a keystore to sign the request:

```python
from simaas.core.keystore import Keystore

keystore = Keystore.from_file('/path/to/keystore.json', password='my-password')
```

#### DORProxy

| Method | Description |
|--------|-------------|
| `search(patterns, owner_iid, data_type, data_format, c_hashes)` | Search for data objects. All parameters optional. |
| `add_data_object(content_path, owner, access_restricted, content_encrypted, data_type, data_format, ...)` | Upload a file as a new data object. Returns `DataObject` with the assigned `obj_id`. |
| `get_meta(obj_id)` | Get metadata for a data object. |
| `get_content(obj_id, with_authorisation_by, download_path)` | Download content to a local file. |
| `delete_data_object(obj_id, with_authorisation_by)` | Remove a data object. |
| `grant_access(obj_id, authority, identity)` | Grant access to a restricted object. |
| `revoke_access(obj_id, authority, identity)` | Revoke access. |
| `update_tags(obj_id, authority, tags)` | Add or update tags. |
| `remove_tags(obj_id, authority, keys)` | Remove tags by key. |
| `get_provenance(c_hash)` | Get provenance by content hash. |
| `statistics()` | Get DOR statistics (available types and formats). |

#### RTIProxy

| Method | Description |
|--------|-------------|
| `get_all_procs()` | List all deployed processors. |
| `get_proc(proc_id)` | Get details of a specific processor. |
| `deploy(proc_id, authority, volumes)` | Deploy a processor. |
| `undeploy(proc_id, authority)` | Undeploy a processor. |
| `submit(tasks, with_authorisation_by)` | Submit one or more tasks. Returns list of `Job`. |
| `get_job_status(job_id, with_authorisation_by)` | Get job status. Returns `JobStatus`. |
| `get_batch_status(batch_id, with_authorisation_by)` | Get batch status. |
| `get_jobs_by_proc(proc_id)` | List jobs for a processor. |
| `get_jobs_by_user(authority, period)` | List jobs by user. Optional `period` in seconds. |
| `cancel_job(job_id, with_authorisation_by)` | Cancel a running job. |
| `purge_job(job_id, with_authorisation_by)` | Force-remove a job. |

#### NodeDBProxy

| Method | Description |
|--------|-------------|
| `get_node()` | Get local node information. |
| `get_network()` | List all known nodes. |
| `get_identity(iid)` | Get an identity by ID. |
| `get_identities()` | List all known identities. |
| `update_identity(identity)` | Create or update an identity. |
| `get_namespace(name)` | Get namespace info. |
| `get_namespaces()` | List all namespaces. |
| `update_namespace_budget(name, budget)` | Set namespace resource budget. |

### Scripting a Pipeline

Here is a complete example that adds a data object, submits a job, polls for completion, and downloads the result:

```python
import time

from simaas.core.keystore import Keystore
from simaas.dor.api import DORProxy
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Task, JobStatus

# Connect and load keystore
dor = DORProxy(('localhost', 5100))
rti = RTIProxy(('localhost', 5100))
keystore = Keystore.from_file('~/.keystore/my-identity.json', password='my-password')

# Upload input data
meta = dor.add_data_object(
    content_path='geometries.geojson',
    owner=keystore.identity,
    access_restricted=False,
    content_encrypted=False,
    data_type='MyProject.GeoVectorData',
    data_format='geojson'
)
geometries_id = meta.obj_id

# Find the deployed processor
procs = rti.get_all_procs()
prep_proc = next(p for p in procs if p.gpp and p.gpp.descriptor['name'] == 'proc-sim-prep')

# Submit a job with one by-value and one by-reference input
task = Task(
    proc_id=prep_proc.id,
    user_iid=keystore.identity.id,
    input=[
        Task.InputValue(
            name='parameters',
            type='value',
            value={'name': 'run-01', 'bbox': [1.2, 103.6, 1.5, 104.0]}
        ),
        Task.InputReference(
            name='geometries',
            type='reference',
            obj_id=geometries_id
        )
    ],
    output=[
        Task.Output(
            name='run_package',
            owner_iid=keystore.identity.id,
            restricted_access=False,
            content_encrypted=False
        )
    ]
)

jobs = rti.submit([task], with_authorisation_by=keystore)
job_id = jobs[0].id

# Poll for completion
while True:
    status = rti.get_job_status(job_id, with_authorisation_by=keystore)
    if status.state in (JobStatus.State.SUCCESSFUL, JobStatus.State.FAILED, JobStatus.State.CANCELLED):
        break
    time.sleep(5)

if status.state == JobStatus.State.SUCCESSFUL:
    # Download the output
    output_obj = status.output['run_package']
    dor.get_content(output_obj.obj_id, with_authorisation_by=keystore, download_path='run_package.tar.gz')
    print(f"Output saved. obj_id={output_obj.obj_id}")
else:
    print(f"Job {status.state}: {status.errors}")
```

### Chaining Jobs in a Script

Extend the pattern to chain multiple stages. After the first job completes, use its output object ID as the input reference for the next:

```python
# After step 1 completes successfully...
run_package_id = status.output['run_package'].obj_id

# Submit step 2
sim_task = Task(
    proc_id=sim_proc.id,
    user_iid=keystore.identity.id,
    input=[
        Task.InputValue(name='parameters', type='value', value={'name': 'run-01'}),
        Task.InputReference(name='run_package', type='reference', obj_id=run_package_id)
    ],
    output=[
        Task.Output(name='results', owner_iid=keystore.identity.id,
                    restricted_access=False, content_encrypted=False)
    ]
)

jobs = rti.submit([sim_task], with_authorisation_by=keystore)
# Poll for completion as before...
```

### REST API

The proxy classes are thin wrappers around the REST API. If you need to call the API directly (e.g., from a non-Python client), the endpoints follow this pattern:

| Service | Base path | Example |
|---------|-----------|---------|
| DOR | `/api/v1/dor` | `GET /api/v1/dor` (search), `POST /api/v1/dor/add` (add), `GET /api/v1/dor/{obj_id}/meta` |
| RTI | `/api/v1/rti` | `GET /api/v1/rti/proc` (list processors), `POST /api/v1/rti/job` (submit), `GET /api/v1/rti/job/{job_id}/status` |
| NodeDB | `/api/v1/db` | `GET /api/v1/db/node` (node info), `GET /api/v1/db/identity` (list identities) |

Write operations require two headers for authentication:

- `saasauth-iid`: the identity ID
- `saasauth-signature`: a signature over `METHOD:URL` + the canonical JSON body, signed with the identity's signing key

The Python proxy classes handle this automatically when you pass a `Keystore` via the `with_authorisation_by` parameter.

## AWS RTI Deployment

The built-in `rti_aws` plugin submits jobs to AWS Batch instead of running them in local Docker containers. This enables execution on cloud infrastructure with elastic compute resources.

### Prerequisites

Build PDIs for the `linux/amd64` architecture:

```bash
simaas-cli image build-local --arch linux/amd64 .
```

### Environment Variables

The AWS RTI requires the following environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `SIMAAS_AWS_REGION` | AWS region | `ap-southeast-1` |
| `SIMAAS_AWS_ROLE_ARN` | IAM role ARN for the service | `arn:aws:iam::123456789:role/simaas-service` |
| `SIMAAS_AWS_ACCESS_KEY_ID` | IAM access key ID | |
| `SIMAAS_AWS_SECRET_ACCESS_KEY` | IAM secret access key | |
| `SIMAAS_AWS_JOB_QUEUE` | AWS Batch job queue name | `simaas-queue` |

### Required IAM Permissions

The IAM user needs these managed policies:

- `AmazonEC2ContainerRegistryFullAccess`
- `AmazonECSTaskExecutionRolePolicy`

Plus these additional permissions:

```json
{
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "logs:CreateLogGroup",
            "Resource": "*"
        }
    ]
}
```

```json
{
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "batch:DescribeJobQueues",
                "batch:CancelJob",
                "batch:SubmitJob",
                "batch:ListJobs",
                "batch:DescribeComputeEnvironments",
                "batch:DeregisterJobDefinition",
                "iam:PassRole",
                "batch:DescribeJobs",
                "batch:ListTagsForResource",
                "batch:RegisterJobDefinition",
                "batch:DescribeSchedulingPolicies",
                "batch:DescribeJobDefinitions",
                "batch:ListSchedulingPolicies",
                "batch:GetJobQueueSnapshot"
            ],
            "Resource": "*"
        }
    ]
}
```

### Starting a Node with AWS RTI

When starting a node interactively, select `Aws` for the RTI service:

```
? Select the type of RTI service: Aws
```

### EFS Volumes

For shared storage between jobs, create EFS volumes:

```bash
simaas-cli rti volume create efs --name shared --efs-fs-id fs-abc123
simaas-cli rti proc deploy --proc-id <proc-obj-id> shared:/data:true
```

### SSH Tunneling for Local Development

When running a node locally but submitting jobs to AWS Batch, the AWS containers need to reach your local node via P2P. An SSH tunnel through an EC2 instance on the same VPC bridges this gap.

Set these environment variables:

```bash
export SSH_TUNNEL_HOST="ec2-aaa-bbb-ccc-ddd.ap-southeast-1.compute.amazonaws.com"  # EC2 public DNS
export SSH_TUNNEL_USER="ubuntu"
export SSH_TUNNEL_KEY_PATH="/path/to/key.pem"
export SIMAAS_CUSTODIAN_HOST="ip-aaa-bbb-ccc-ddd.ap-southeast-1.compute.internal"  # EC2 private DNS
```

The SSH tunnel is established automatically when the AWS RTI service is selected and all tunnel variables are defined. The `SIMAAS_CUSTODIAN_HOST` overrides the node's domain name so that AWS Batch containers connect through the EC2 instance's private address rather than trying to reach your local machine directly.
