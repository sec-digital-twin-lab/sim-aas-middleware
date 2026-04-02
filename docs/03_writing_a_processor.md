# Writing a Processor

A processor is a containerised computational model with a formal I/O interface. This guide covers everything you need to create one: the descriptor, the Python implementation, the Dockerfile, and advanced patterns like co-simulation and child job spawning.

### Processors and Adapters

In practice, many processors act as **adapters** — they bridge between the middleware's data object model and an external tool or data source. For example, an adapter might wrap an existing simulation engine by translating DOR inputs into the engine's native format, running it, and converting the results back. Or it might fetch data from a public API and write it as a DOR-compatible output. The implementation techniques are identical — `ProcessorBase`, descriptors, Dockerfiles — but the mental model is different: an adapter's job is translation and orchestration rather than implementing computational logic from scratch. When building a system of multiple adapters, each one handles a well-defined transformation step, and the middleware chains them through their data type contracts (see [Building Pipelines](05_running_jobs.md#building-pipelines)).

## Processor Directory Structure

A processor lives in a directory with at minimum these files:

```
my-processor/
  descriptor.json      # I/O interface declaration
  processor.py         # Python implementation
  Dockerfile           # Container build instructions
  requirements.txt     # Python dependencies (if any)
```

## The Descriptor

`descriptor.json` defines the processor's contract: what it consumes and produces. The middleware uses this for input validation at submission time, output verification after execution, and provenance tracking.

### Basic Example

From the `abc` example (`examples/simple/abc/descriptor.json`):

```json
{
  "name": "proc-abc",
  "input": [
    {
      "name": "a",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    },
    {
      "name": "b",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    }
  ],
  "output": [
    {
      "name": "c",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    }
  ],
  "required_secrets": ["SECRET_ABC_KEY"]
}
```

### Descriptor Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Processor name. Used as the Docker image name prefix. |
| `input` | Yes | List of input data object declarations. |
| `output` | Yes | List of output data object declarations. |
| `required_secrets` | Yes | List of environment variable names the processor needs at runtime. Can be empty `[]`. |

### IODataObject Fields

Each input and output entry has:

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | -- | Identifier for this data object. Must be unique within input or output. Used as the filename in the working directory. |
| `data_type` | Yes | -- | Logical type (e.g., `"JSONObject"`, `"Image"`, `"CSV"`). Used for matching, not interpreted by the middleware. |
| `data_format` | Yes | -- | File format (e.g., `"json"`, `"png"`, `"csv"`). Used for matching, not interpreted by the middleware. |
| `data_schema` | No | `null` | JSON Schema for validating content. Only applicable to JSON data. Validated by the job runner before and after execution. |
| `optional` | No | `false` | If `true`, this input/output may be absent without causing a validation error. |

### Naming Conventions

- Use `snake_case` for data object names
- Don't include file extensions in names (the name *is* the filename in the working directory)
- Don't encode type or format information in the name

### Data Type Matching

The middleware does not interpret `data_type` or `data_format`; they are opaque strings used for matching. When a job is submitted, the middleware checks that each input data object's type and format match what the processor expects.

**Wildcard matching** is supported on input declarations using a trailing `*`:
- `"Sensor.Temperature*"` matches `"Sensor.TemperatureIndoor"`, `"Sensor.TemperatureOutdoor"`, etc.
- `"*"` matches any data type or format

Wildcards are only valid on input declarations, not outputs.

### Optional Data Objects

Setting `optional: true` allows flexible processor contracts:

```json
{
  "name": "proc-defg",
  "input": [
    {"name": "d", "data_type": "JSONObject", "data_format": "json", "optional": true},
    {"name": "e", "data_type": "JSONObject", "data_format": "json", "optional": true}
  ],
  "output": [
    {"name": "f", "data_type": "JSONObject", "data_format": "json", "optional": true},
    {"name": "g", "data_type": "JSONObject", "data_format": "json", "optional": true}
  ],
  "required_secrets": []
}
```

At submission time, optional inputs don't need to be provided and optional outputs don't need to be declared. After execution, the middleware only verifies that declared (non-optional) outputs were produced.

### Secrets

The `required_secrets` field lists environment variable names that the RTI must provide at runtime. During the job runner's handshake with the custodian node, the custodian reads these variables from its own environment and sends them to the container. The processor accesses them via `os.environ`.

This is useful for API keys, credentials, or configuration that shouldn't be baked into the Docker image or stored in the DOR.

## The Python Implementation

`processor.py` must contain a class that extends `ProcessorBase`:

```python
import json
import logging
import os

from simaas.rti.schemas import Job
from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace


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
        # Read inputs from the working directory
        with open(os.path.join(wd_path, 'my_input'), 'r') as f:
            data = json.load(f)

        # Do computation
        result = do_something(data)

        # Write output to the working directory
        with open(os.path.join(wd_path, 'my_output'), 'w') as f:
            json.dump(result, f)

        # Notify the middleware that the output is ready
        listener.on_output_available('my_output')
        listener.on_progress_update(100)

    def interrupt(self) -> None:
        self._is_cancelled = True
```

### The `run()` Method

Parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `wd_path` | `str` | Path to the working directory. Input files are placed here by the job runner, named exactly as declared in the descriptor. Write output files here with exact descriptor names. |
| `job` | `Job` | Job metadata including ID, batch ID, and task details. |
| `listener` | `ProgressListener` | Callback interface for reporting progress, output availability, and messages. |
| `namespace` | `Namespace` | Runtime API for accessing DOR, RTI, and identity services. See [Namespace API](#namespace-api) below. |
| `logger` | `logging.Logger` | Logger instance for the processor. |

### Reading Inputs

Inputs are files in `wd_path` named exactly as declared in the descriptor. For the abc example with inputs `a` and `b`:

```python
a_path = os.path.join(wd_path, 'a')
with open(a_path, 'r') as f:
    a = json.load(f)
```

For optional inputs, check if the file exists:

```python
d_path = os.path.join(wd_path, 'd')
if os.path.isfile(d_path):
    with open(d_path, 'r') as f:
        d = json.load(f)
```

### Writing Outputs

Write output files to `wd_path` with names matching the descriptor, then notify the middleware:

```python
c_path = os.path.join(wd_path, 'c')
with open(c_path, 'w') as f:
    json.dump({'v': result}, f)

listener.on_output_available('c')
```

The `on_output_available()` call triggers the job runner to push the output to the DOR with a provenance record. Call it once per output, after the file is fully written.

For optional outputs, check if the output was declared in the task before writing:

```python
if 'f' in [o.name for o in job.task.output]:
    # write f and notify
```

### Progress and Messages

Report progress (0-100) and messages throughout execution:

```python
listener.on_progress_update(0)
listener.on_message(Severity.INFO, "Starting computation...")

# ... work ...

listener.on_progress_update(50)
listener.on_message(Severity.INFO, "Halfway done.")

# ... more work ...

listener.on_progress_update(100)
```

Progress and messages are pushed back to the custodian node and visible via `simaas-cli rti job status` and `simaas-cli rti job logs`.

### Cancellation

Implement `interrupt()` to support cancellation. The standard pattern:

```python
def __init__(self, proc_path: str) -> None:
    super().__init__(proc_path)
    self._is_cancelled = False

def run(self, wd_path, job, listener, namespace, logger):
    # Check periodically
    for step in work:
        if self._is_cancelled:
            return
        process(step)

def interrupt(self) -> None:
    self._is_cancelled = True
```

When `interrupt()` is called, `run()` should exit cleanly as soon as possible.

### Error Handling

Raise `ProcessorRuntimeError` for expected failures:

```python
from simaas.core.processor import ProcessorRuntimeError

if not valid:
    raise ProcessorRuntimeError("Invalid input data", details={'field': 'x', 'reason': 'out of range'})
```

The `details` dict must be JSON-serialisable. It will appear in the job status error field.

## Namespace API

The `namespace` parameter gives processors runtime access to the middleware's services. This enables two additional patterns: co-simulation and dynamic child job spawning.

### RTI Operations

| Method | Description |
|--------|-------------|
| `namespace.rti.submit(tasks)` | Submit one or more tasks. Returns a list of `Job` objects. |
| `namespace.rti.get_job_status(job_id)` | Get the current status of a job. |
| `namespace.rti.get_batch_status(batch_id)` | Get status of all jobs in a batch, including network addresses and port mappings. |
| `namespace.rti.get_all_procs()` | List all deployed processors. |
| `namespace.rti.get_proc(proc_id)` | Get details of a specific processor. |
| `namespace.rti.job_cancel(job_id)` | Cancel a running job. |
| `namespace.rti.job_purge(job_id)` | Purge a job regardless of state. |

### DOR Operations

| Method | Description |
|--------|-------------|
| `namespace.dor.get_content(obj_id, content_path)` | Download a data object's content to a local file. |
| `namespace.dor.get_meta(obj_id)` | Get metadata for a data object. |
| `namespace.dor.get_provenance(c_hash)` | Get the provenance graph for a content hash. |
| `namespace.dor.search(patterns, ...)` | Search for data objects. |
| `namespace.dor.add(...)` | Add a new data object. |
| `namespace.dor.remove(obj_id)` | Remove a data object. |
| `namespace.dor.grant_access(obj_id, iid)` | Grant access to a restricted data object. |
| `namespace.dor.revoke_access(obj_id, iid)` | Revoke access. |

### Identity

| Method | Description |
|--------|-------------|
| `namespace.keystore()` | Access the keystore (signing, encryption, identity). |
| `namespace.custodian_address()` | Get the custodian node's P2P address. |

## Co-Simulation Pattern

To enable co-simulation, processors are submitted together as a **batch**. Each processor can then discover the others' network addresses through `get_batch_status()`.

The room/thermostat example (`examples/cosim/`) demonstrates this:

**Room processor** (opens a TCP server socket):

```python
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(('0.0.0.0', 7001))
server.listen(1)
conn, _ = server.accept()

# Simulation loop: send temperature, receive command
while step < max_steps:
    conn.sendall(f"STEP:{step},TEMP:{temp}".encode())
    command = conn.recv(1024).decode().strip()
    # Update temperature based on command...
```

**Thermostat processor** (discovers the room's address and connects):

```python
# Get batch status to find the room simulator's address
batch_status = namespace.rti.get_batch_status(job.batch_id)
members = {m.name: m for m in batch_status.members}
room_address = members['room'].ports['7001/tcp']
host, port = parse_address(room_address)

# Connect and start co-simulation
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect((host, port))

# Simulation loop: receive temperature, send command
while True:
    data = client.recv(1024).decode()
    # Decide heater command based on temperature...
    client.sendall(command.encode())
```

The Dockerfile must expose the co-simulation port (in addition to port 6000 for P2P):

```dockerfile
EXPOSE 6000 7001
```

## Dynamic Child Job Spawning Pattern

A running processor can submit jobs to other deployed processors through the Namespace API.

The prime factorisation example (`examples/prime/`) demonstrates this:

```python
# Discover the factor-search processor
procs = namespace.rti.get_all_procs()
proc_id = next(p.id for p in procs if 'proc-factor-search' in p.image_name)

# Submit child jobs for each search range
pending = []
for i in range(num_sub_jobs):
    task = Task(
        proc_id=proc_id,
        user_iid=namespace.keystore().identity.id,
        input=[Task.InputValue(
            name='parameters',
            type='value',
            value={'start': start, 'end': end, 'number': N}
        )],
        output=[Task.Output(
            name='result',
            owner_iid=namespace.keystore().identity.id,
            restricted_access=False,
            content_encrypted=False,
            target_node_iid=None
        )],
        budget=ResourceDescriptor(vcpus=1, memory=2048),
        namespace=namespace.name(),
    )
    jobs = namespace.rti.submit([task])
    pending.append(jobs[0])

# Monitor and collect results
while pending:
    job = pending[0]
    status = namespace.rti.get_job_status(job.id)
    if status.state == JobStatus.State.SUCCESSFUL:
        pending.pop(0)
        # Retrieve result from DOR
        result_obj = status.output['result']
        namespace.dor.get_content(result_obj.obj_id, local_path)
        # Process result...
```

Note the use of `Task.InputValue` with `type='value'`, which passes data inline as JSON rather than by reference to a DOR object. Useful for dynamically generated parameters.

## The Dockerfile

The Dockerfile packages the processor into a Docker image that the middleware can execute. Use a multi-stage build:

```dockerfile
# -------- Builder stage ----------
FROM python:3.12.10-slim-bullseye AS builder

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git build-essential libffi-dev tzdata && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

COPY . /processor
RUN mv /processor/sim-aas-middleware /sim-aas-middleware

RUN /opt/venv/bin/pip install --no-cache-dir /sim-aas-middleware

WORKDIR /processor
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# -------- Runtime stage ----------
FROM python:3.12.10-slim-bullseye

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /processor /processor

ENV PATH="/opt/venv/bin:${PATH}"

EXPOSE 6000 7000

ENTRYPOINT ["simaas-cli","--log-console","run",\
            "--job-path","/job",\
            "--proc-path","/processor",\
            "--service-address","tcp://0.0.0.0:6000"]
```

Key points:

- **Port 6000** is mandatory; it is used for P2P communication between the container and the custodian node.
- **Additional ports** (7000, 7001, etc.) are for custom inter-processor communication (co-simulation).
- The `COPY . /processor` and `RUN mv /processor/sim-aas-middleware /sim-aas-middleware` pattern is how the middleware's build system injects the middleware source into the image context.
- The entrypoint runs the middleware's job runner, which handles all the lifecycle management (fetching inputs, running the processor, pushing outputs).

### Optional: Test Stage

You can add a test stage to run unit tests inside the container:

```dockerfile
FROM python:3.12.10-slim-bullseye AS test

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /processor /processor

ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /processor
RUN if [ -f requirements-dev.txt ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

EXPOSE 6000 7000
ENTRYPOINT ["simaas-cli","--log-console","run",\
            "--job-path","/job",\
            "--proc-path","/processor",\
            "--service-address","tcp://0.0.0.0:6000"]
```

Build the test image with `--target test` and run tests via:

```bash
simaas-cli image build-local --target test --arch linux/amd64 .
docker run --rm --entrypoint pytest <image-name> /processor/test_processor.py -v
```
