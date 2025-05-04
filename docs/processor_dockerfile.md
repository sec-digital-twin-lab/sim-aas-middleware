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
The following is an example and recommended template for processor dockerfiles:
```shell
FROM ubuntu:24.04

# Install system packages and Python 3.12
RUN apt update -y && \
    apt install -y tzdata software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt update -y && \
    apt install -y python3.12 python3-pip python3.12-venv git

# Clone sim-aas-middleware (optionally using Git credentials)
RUN --mount=type=secret,id=git_credentials \
    if [ -f /run/secrets/git_credentials ]; then \
        GIT_CREDENTIALS=$(cat /run/secrets/git_credentials); \
        git clone https://$GIT_CREDENTIALS@github.com/sec-digital-twin-lab/sim-aas-middleware; \
    else \
        git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware; \
    fi

# Checkout specific commit
WORKDIR /sim-aas-middleware
RUN git checkout f98a2b97622da01b08857977c94859424cd4fd3c

# Install processor code
WORKDIR /processor
COPY . .

# Set up virtual environment
RUN python3.12 -m venv /venv
RUN /venv/bin/pip install --upgrade pip setuptools
RUN /venv/bin/pip install -r requirements.txt
RUN /venv/bin/pip install /sim-aas-middleware

# Create job working directory
RUN mkdir /job

# Required for P2P communication
EXPOSE 6000

# Optional: expose additional ports for custom communication
EXPOSE 7001

# Start job runner
ENTRYPOINT ["/venv/bin/simaas-cli", "--log-console", "run", "--job-path", "/job", "--proc-path", "/processor", "--service-address", "tcp://0.0.0.0:6000"]
```

> Note:  the `git checkout f98a...` part should be modified to point at
> the specific version of the middleware that is being used. Since the Sim-aaS Middleware is under
> active development, this may have to change frequently.