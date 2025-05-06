########################################
# -------- 1. Builder stage ----------
########################################
FROM --platform=$TARGETPLATFORM python:3.12.10-slim-bullseye AS builder

# for logging
ARG TARGETPLATFORM
ARG BUILDPLATFORM
RUN echo "🔧 Building for $TARGETPLATFORM on $BUILDPLATFORM"

# Build-time packages – remove later
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git build-essential libffi-dev tzdata && \
    rm -rf /var/lib/apt/lists/*

# Clone middleware (with optional secret token)
RUN --mount=type=secret,id=git_credentials \
    if [ -f /run/secrets/git_credentials ]; then \
        git clone https://$(cat /run/secrets/git_credentials)@github.com/sec-digital-twin-lab/sim-aas-middleware /sim-aas-middleware ;\
    else \
        git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware /sim-aas-middleware ;\
    fi && \
    cd /sim-aas-middleware && \
    git checkout 33e81110431b5a088643f44ac0d846f080f0699b

# Virtual-env with everything pre-built for TARGETPLATFORM
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir /sim-aas-middleware

# Processor code + its own deps
WORKDIR /processor
COPY . /processor
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

########################################
# -------- 2. Runtime stage ----------
########################################
FROM --platform=$TARGETPLATFORM python:3.12.10-slim-bullseye

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /processor /processor

ENV PATH="/opt/venv/bin:${PATH}"

RUN mkdir /job
EXPOSE 6000 7000

ENTRYPOINT ["simaas-cli","--log-console","run",\
            "--job-path","/job",\
            "--proc-path","/processor",\
            "--service-address","tcp://0.0.0.0:6000"]
