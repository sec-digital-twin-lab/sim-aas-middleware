# Getting Started

This guide walks you through the complete workflow: from installation to running your first job and retrieving the results. It uses the `abc` example processor, which takes two numbers as input and produces their sum.

## Prerequisites

- Python 3.13+
- Docker (running)
- Linux or macOS
- Git

## Step 1: Install the Middleware

```bash
git clone https://github.com/simaas/sim-aas-middleware.git
cd sim-aas-middleware
python -m venv .venv
source .venv/bin/activate
pip install .
```

Verify the installation:

```bash
simaas-cli --help
```

## Step 2: Create an Identity

Every interaction with Sim-aaS requires a cryptographic identity. Create one:

```bash
simaas-cli identity create
```

The interactive prompt asks for a name, email, and password. This creates a keystore file in `~/.keystore/` containing your private keys and identity.

Verify it was created:

```bash
simaas-cli identity list
```

## Step 3: Start a Node

A Sim-aaS node is a service instance that provides data storage (DOR) and job execution (RTI). The Docker RTI requires the `SIMAAS_REPO_PATH` environment variable pointing to the middleware repository root:

```bash
export SIMAAS_REPO_PATH=/path/to/sim-aas-middleware
```

Start a node using the `dev` profile, which enables all services with sensible defaults for local development:

```bash
simaas-cli service --profile dev
```

The node starts and shows its REST and P2P addresses. Keep this terminal running.

The `dev` profile configures:
- DOR plugin: `fs` (SQLite + filesystem storage)
- RTI plugin: `docker` (local Docker execution)
- Non-strict deployment (any identity can deploy processors)
- Job history retention (for debugging)

Other profiles: `prod` (binds all interfaces, strict deployment), `minimal` (no DOR/RTI), `storage` (DOR only, no job execution).

You can also configure each option individually instead of using a profile. Run `simaas-cli service --help` for all options.

## Step 4: Build the Processor Docker Image

Open a new terminal (keep the node running, make sure the virtual environment is activated) and navigate to the abc example:

```bash
cd examples/simple/abc
```

Build the Processor Docker Image (PDI):

```bash
simaas-cli image build-local --arch linux/amd64 .
```

This builds a Docker image containing the processor code and the middleware's job runner, and exports it as a `.pdi` file. The `SIMAAS_REPO_PATH` environment variable must point to the middleware repository root (or you will be prompted for it).

Now import the PDI into the DOR:

```bash
simaas-cli image import <path-to-pdi-file>
```

This prompts for the node's REST address and your keystore/password, then uploads the image to the DOR. Note the object ID in the output; you will use it to deploy.

## Step 5: Upload Input Data

The abc processor expects two JSON input files. Example data is provided in `examples/simple/abc/data/`:

`data_object_a.json`:
```json
{"v": 1}
```

`data_object_b.json`:
```json
{"v": 2}
```

Add them to the DOR:

```bash
simaas-cli dor add --data-type JSONObject --data-format json --assume-creator data/data_object_a.json
simaas-cli dor add --data-type JSONObject --data-format json --assume-creator data/data_object_b.json
```

The `--assume-creator` flag skips the interactive co-creator selection prompt and records your identity as the creator.

Each command returns metadata including an `obj_id`. Note these; you will reference them when submitting the job.

## Step 6: Deploy the Processor

Deploy the processor on the RTI so it can accept jobs:

```bash
simaas-cli rti proc deploy
```

The interactive prompt lists available processor images from the DOR. Select the `proc-abc` image you just uploaded. After deployment, verify:

```bash
simaas-cli rti proc list
```

You should see `proc-abc` with state `State.READY`.

## Step 7: Submit a Job

Submit a job to the deployed processor:

```bash
simaas-cli rti job submit
```

The interactive prompt walks you through:
1. Selecting the processor (`proc-abc`)
2. Specifying inputs (choose "by-reference" and select the data objects you uploaded)
3. Configuring output ownership and access
4. Setting a resource budget (CPU and memory for the container)

Note the job ID in the output.

## Step 8: Monitor the Job

Check the job status:

```bash
simaas-cli rti job status <job-id>
```

The status progresses through: `initialised` -> `running` -> `successful` (or `failed`).

For more detail:

```bash
simaas-cli rti job inspect <job-id>    # detailed job information
simaas-cli rti job logs <job-id>       # progress messages from the processor
```

## Step 9: Retrieve the Result

Once the job is `successful`, the status output includes the output data objects. Download the result using its object ID:

```bash
simaas-cli dor download <output-obj-id>
```

The downloaded file contains:

```json
{"v": 3}
```

That's `a + b = 1 + 2 = 3`. The result is also stored in the DOR with full provenance: a record of exactly which processor version and which inputs produced it.

## What Just Happened

Behind the scenes, the middleware:

1. Built a Docker image containing the processor code and the middleware's job runner, exported it as a PDI file
2. Imported the PDI into the DOR as a content-addressed data object
3. On deployment, loaded the image into Docker and registered the processor with the RTI
4. On job submission, validated inputs against the processor's descriptor, created a container, and started it
5. Inside the container, the job runner fetched input data, ran the processor, and pushed outputs back to the DOR
6. Created a provenance record linking the output to the exact processor version (Git commit) and input content hashes

## Next Steps

- [Concepts](02_concepts.md): understand the core abstractions
- [Writing a Processor](03_writing_a_processor.md): create your own processor
- [Co-simulation example](../examples/cosim/): see processors communicating at runtime
- [Prime factorisation example](../examples/prime/): see dynamic child job spawning
