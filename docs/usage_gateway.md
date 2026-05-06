# Gateway

The Gateway provides a REST API for applications to interact with a Sim-aaS node. It handles
authentication via API keys and translates requests into authenticated Sim-aaS calls using
internally managed keystores. This allows applications to manage data and run jobs without
dealing with Sim-aaS cryptographic identity directly.

## Architecture

```
Application
  -> Gateway REST API (API key auth)
    -> Sim-aaS Node (keystore-based auth)
```

The Gateway runs as a separate process that connects to a Sim-aaS node over HTTP. Each
Gateway user account is associated with a keystore. When an application makes a request
with an API key, the Gateway resolves the associated keystore and uses it to sign the
corresponding Sim-aaS API call.

## Setup

### Prerequisites

- A running Sim-aaS node with at least a DOR service. If you want to run jobs via the
  gateway, the node also needs an RTI service (e.g., Docker) with processors deployed.
  See [Running a Sim-aaS Node](usage_run_simaas_node.md) for details.
- The node must be reachable on a network address (not `127.0.0.1`) if Docker-based
  processors need to connect back to the node for job execution.

### 1. Start a Sim-aaS Node

Before starting the node, create an identity if you don't have one:
```shell
simaas-cli identity create --name 'my-node' --email 'node@example.com'
```

Note the identity ID from the output. You will need it for the `--keystore-id` flag
when running non-interactively.

Start the node using the `--profile dev` shortcut (enables DOR + Docker RTI with
sensible defaults):
```shell
simaas-cli --keystore ~/.simaas/keystore --keystore-id <ID> --password '<password>' \
  service node --profile dev \
  --datastore ~/.simaas/node \
  --rest-address 192.168.1.38:5001 \
  --p2p-address tcp://192.168.1.38:4001 \
  --boot-node 192.168.1.38:5001 \
  --simaas-repo-path /path/to/sim-aas-middleware
```

> **Note:** Use your machine's local network IP (not `127.0.0.1` or `localhost`).
> Docker containers running processors need to reach the node for the P2P handshake.
> The `--simaas-repo-path` flag (or `SIMAAS_REPO_PATH` environment variable) is required
> when using the Docker RTI, as the node may need to build processor images.

### 2. Create a Gateway User Account

Create a user account in the Gateway. This generates a keystore and publishes the identity
to the connected Sim-aaS node.

Interactive:
```shell
simaas-cli gateway --address 192.168.1.38:5001 user create
```

Non-interactive:
```shell
simaas-cli gateway --address 192.168.1.38:5001 --datastore ~/.simaas/gateway \
  user create --name 'my-app' --email 'my-app@example.com' --password 'secret'
```

Output:
```
New user created!
- UUID: 777ddd25-60cb-4db8-8de8-c0cf0c29bf99
- Identity: my-app/my-app@example.com/emv2o4cqjrw77h3cffun3vj9q06zh264...
```

### 3. Generate an API Key

Interactive:
```shell
simaas-cli gateway --address 192.168.1.38:5001 key create
```

Non-interactive (using the UUID from step 2, without dashes):
```shell
simaas-cli gateway --address 192.168.1.38:5001 --datastore ~/.simaas/gateway \
  key create --uuid 777ddd2560cb4db88de8c0cf0c29bf99 --description 'production key'
```

Output:
```
New API key created!
- UUID: 777ddd25-60cb-4db8-8de8-c0cf0c29bf99
- Key: 777ddd2560cb4db88de8c0cf0c29bf992d10adeaf34869f91ae9008a69ad98a7
- Description: production key
```

Save this key — it is the Bearer token used for all API calls.

### 4. Start the Gateway Service

Interactive:
```shell
simaas-cli service gateway --address 192.168.1.38:5001
```

Non-interactive:
```shell
simaas-cli service gateway \
  --address 192.168.1.38:5001 \
  --datastore ~/.simaas/gateway \
  --service-address 192.168.1.38:5101
```

Output:
```
INFO:     Uvicorn running on http://192.168.1.38:5101 (Press CTRL+C to quit)
```

The Gateway is now running at `http://192.168.1.38:5101`.

> **Important:** The `--datastore` path must be the same across all gateway commands
> (`service gateway`, `gateway user`, `gateway key`). They share the same SQLite database.
> If you omit it, each command defaults to `~/.simaas/gateway`.

### 5. Verify

```shell
curl -s -H "Authorization: Bearer <api_key>" http://192.168.1.38:5101/gateway/v1/data
```

Should return `[]` (empty list of data objects).

### Deploying Processors

The Gateway intentionally does not expose processor deployment — that is an admin operation
done directly on the node. To make processors available through the Gateway:

1. Build a Processor Docker Image (PDI):
   ```shell
   simaas-cli image build-local /path/to/processor ~/proc-name.pdi \
     --simaas-repo-path /path/to/sim-aas-middleware
   ```

2. Import the PDI into the node's DOR:
   ```shell
   simaas-cli --keystore ~/.simaas/keystore --keystore-id <ID> --password '<password>' \
     image import --address 192.168.1.38:5001 ~/proc-name.pdi
   ```

3. Deploy the processor (using the object ID from the import output):
   ```shell
   simaas-cli --keystore ~/.simaas/keystore --keystore-id <ID> --password '<password>' \
     rti --address 192.168.1.38:5001 proc deploy
   ```

Once deployed, the processor will appear in `GET /gateway/v1/proc` and can be used to
submit jobs through the Gateway.

## REST API

All endpoints require an API key via the `Authorization` header:

```
Authorization: Bearer <api_key>
```

### Data Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/gateway/v1/data` | Search data objects (scoped to authenticated user) |
| `POST` | `/gateway/v1/data` | Upload a data object |
| `GET` | `/gateway/v1/data/{obj_id}/meta` | Get data object metadata |
| `GET` | `/gateway/v1/data/{obj_id}/content` | Download data object content |
| `DELETE` | `/gateway/v1/data/{obj_id}` | Delete a data object |
| `PUT` | `/gateway/v1/data/{obj_id}/tags` | Add or update tags |
| `DELETE` | `/gateway/v1/data/{obj_id}/tags` | Remove tags |

### Job Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/gateway/v1/proc` | List available processors |
| `POST` | `/gateway/v1/proc/{proc_id}` | Submit a job |
| `GET` | `/gateway/v1/job` | List jobs for authenticated user |
| `GET` | `/gateway/v1/job/{job_id}` | Get job status |
| `DELETE` | `/gateway/v1/job/{job_id}` | Cancel a job |

## Examples

The following examples use the Python client SDK (`simaas.gateway.helpers`). All functions
take `address` as a URL string (e.g., `http://localhost:5101`) and `api_key` as a string.

### Upload and Download Data

```python
from simaas.gateway import helpers

address = "http://localhost:5101"
api_key = "29b239bc387845ae..."

# upload a JSON file
obj = helpers.upload(
    address, api_key,
    content_path="data/input_a.json",
    data_type="JSONObject",
    data_format="json",
    tags={"experiment": "run-42"}
)
print(f"Uploaded: {obj.obj_id}")

# search for data objects
results = helpers.search(address, api_key, data_type="JSONObject")
for r in results:
    print(f"  {r.obj_id} [{r.data_type}:{r.data_format}]")

# download content
helpers.download(address, api_key, obj.obj_id, content_path="output/downloaded.json")

# get metadata
meta = helpers.meta(address, api_key, obj.obj_id)
print(f"Owner: {meta.owner_iid}, Tags: {meta.tags}")
```

### Manage Tags

```python
# add tags
helpers.tag(address, api_key, obj.obj_id, tags={"status": "reviewed", "version": "2"})

# remove tags
helpers.untag(address, api_key, obj.obj_id, keys=["status"])

# delete the data object
helpers.delete(address, api_key, obj.obj_id)
```

### Submit a Job and Monitor Status

```python
import time
from simaas.gateway import helpers
from simaas.rti.schemas import JobStatus

address = "http://localhost:5101"
api_key = "29b239bc387845ae..."

# list available processors
procs = helpers.available_procs(address, api_key)
for proc_id, gpp in procs.items():
    print(f"  {proc_id}: {gpp.repository}")

# submit a job with by-value inputs
job = helpers.submit(
    address, api_key,
    proc_id="abc-processor-id",
    task_input=[
        {"name": "a", "type": "value", "value": {"v": 3}},
        {"name": "b", "type": "value", "value": {"v": 4}},
    ],
    task_output=[
        {"name": "c"},
    ],
    name="my-job",
    description="Adding 3 + 4",
)
print(f"Job submitted: {job.id}")

# poll for completion
while True:
    status = helpers.job_status(address, api_key, job.id)
    print(f"  State: {status.state}, Progress: {status.progress}%")
    if status.state in (JobStatus.State.SUCCESSFUL, JobStatus.State.FAILED, JobStatus.State.CANCELLED):
        break
    time.sleep(2)

# check result
if status.state == JobStatus.State.SUCCESSFUL:
    output_obj = status.output["c"]
    helpers.download(address, api_key, output_obj.obj_id, "result.json")
    print(f"Result saved to result.json")
else:
    print(f"Job failed: {[e.message for e in status.errors]}")
```

### Submit a Job with By-Reference Inputs

If input data is already stored in the DOR, reference it by object ID:

```python
job = helpers.submit(
    address, api_key,
    proc_id="my-processor-id",
    task_input=[
        {"name": "input_data", "type": "reference", "obj_id": "9f5b...a85c"},
    ],
    task_output=[
        {"name": "result"},
    ],
)
```

### Submit a Job with Custom Resource Budget

By default, jobs are allocated 1 vCPU and 2048 MB of memory. Override this for
resource-intensive jobs:

```python
job = helpers.submit(
    address, api_key,
    proc_id="heavy-processor-id",
    task_input=[...],
    task_output=[...],
    budget={"vcpus": 4, "memory": 8192},
)
```

### List Jobs

```python
my_jobs = helpers.jobs(address, api_key)
for job in my_jobs:
    print(f"  {job.id}: {job.proc_name} (submitted: {job.t_submitted})")
```

### Cancel a Job

```python
status = helpers.cancel(address, api_key, job_id="abc123")
print(f"Job state after cancel: {status.state}")
```

## CLI Reference

### Service

```shell
simaas-cli service gateway [options]
```

| Option | Description |
|--------|-------------|
| `--datastore` | Path to gateway datastore (default: `~/.simaas/gateway`) |
| `--address` | REST address of the Sim-aaS node to connect to |
| `--service-address` | Address for the gateway REST service to bind to |

### User Management

```shell
simaas-cli gateway [--datastore PATH] [--address HOST:PORT] user <command>
```

| Command | Description |
|---------|-------------|
| `create` | Create a new user account (generates keystore, publishes identity) |
| `list` | List all user accounts |
| `delete` | Delete user accounts |
| `enable` | Enable user accounts |
| `disable` | Disable user accounts |
| `publish` | Re-publish user identities to the Sim-aaS node |

### API Key Management

```shell
simaas-cli gateway [--datastore PATH] [--address HOST:PORT] key <command>
```

| Command | Description |
|---------|-------------|
| `create` | Generate a new API key for a user |
| `list` | List all API keys |
| `delete` | Delete API keys |

## Storage

The Gateway stores its data in a configurable directory (default: `~/.simaas/gateway/`).
This directory contains a single SQLite database (`db.dat`) holding user accounts and API
keys. Each user account includes a serialized keystore.

## Migration from Substrate API

For applications that previously used the `substrate-api` package:

1. Replace the dependency: `substrate-api @ git+...` with `simaas` (the gateway is included)
2. Update imports: `substrate.api.helpers` becomes `simaas.gateway.helpers`
3. Update the base URL: `/substrate/v1/` becomes `/gateway/v1/`
4. The upload file field is now `attachment` (was `content`)
5. New functionality available: `helpers.jobs()` for listing jobs, optional `budget` parameter on `helpers.submit()`
