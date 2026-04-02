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
