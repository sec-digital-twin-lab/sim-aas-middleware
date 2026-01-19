# Processor Dockerfile
The Dockerfile defines how to build a **Processor Docker Image (PDI)**, which encapsulates a 
self-contained processor along with its dependencies and execution logic. The Docker image 
is intended to be executed by the **job runner**, not directly by the RTI. The job runner 
(invoked via the `simaas-cli` run command) handles all aspects of initialization, data 
ingestion, P2P communication, and processor execution.

## Required Structure
A valid Dockerfile must follow the required conventions below:

### Entrypoint
Each processor container must use the following CLI-based entry point:
```
ENTRYPOINT ["/venv/bin/simaas-cli", "--log-console", "run", "--job-path", "/job", "--proc-path", "/processor", "--service-address", "tcp://0.0.0.0:6000"]
```
This runs the job runner, which initializes the processor, handles communication with the 
RTI (via P2P protocols), validates input/output data, and coordinates the execution lifecycle.

### P2P Communication Port (6000)
Port 6000 is the mandatory default port for P2P communication between the RTI and the 
processor container.
```
EXPOSE 6000
```
This port is used for establishing a secure socket connection through which the RTI primes 
the processor with the job description and encrypted credentials.

### Additional Ports for Processor Logic
Processors may define additional ports to support use cases like inter-processor 
communication, especially in scenarios such as co-simulation.
```
EXPOSE 7001  # Example custom port for job-to-job communication
```
These ports are not managed by the Sim-aaS middleware. If required, the processor must 
implement and expose its own communication protocols (e.g., custom TCP server, gRPC, 
ZeroMQ, etc.) for inter-container messaging.

### Typical Build Steps
A standard processor Dockerfile typically:
1. Installs system and Python dependencies.
2. Optionally clones and installs the Sim-aaS Middleware from GitHub.
3. Copies the processor implementation (`processor.py`, `descriptor.json`, etc.) into the image.
4. Installs processor-specific Python requirements.
5. Prepares a working directory (`/job`) for job data.


## Example
The following is an example and recommended template for processor dockerfiles using a multi-stage build:
```dockerfile
########################################
# -------- 1. Builder stage ----------
########################################
FROM python:3.13-slim-bookworm AS builder

# Build-time packages
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git build-essential libffi-dev tzdata && \
    rm -rf /var/lib/apt/lists/*

# Virtual-env with everything pre-built
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy the processor and move the sim-aas-middleware out
COPY . /processor
RUN mv /processor/sim-aas-middleware /sim-aas-middleware

# Install sim-aas-middleware
RUN /opt/venv/bin/pip install --no-cache-dir /sim-aas-middleware

# Processor code + its own deps
WORKDIR /processor
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

########################################
# -------- 2. Runtime stage ----------
########################################
FROM python:3.13-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /processor /processor

ENV PATH="/opt/venv/bin:${PATH}"

# Create job working directory
RUN mkdir /job

# Required for P2P communication
EXPOSE 6000

# Optional: expose additional ports for custom communication
EXPOSE 7001

# Start job runner
ENTRYPOINT ["simaas-cli", "--log-console", "run", "--job-path", "/job", "--proc-path", "/processor", "--service-address", "tcp://0.0.0.0:6000"]
```

> Note: The sim-aas-middleware is copied into the build context and installed during the build process.
> The build system automatically includes the middleware sources when building processor images.