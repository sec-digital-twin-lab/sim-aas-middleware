########################################
# -------- 1. Builder stage ----------
########################################
FROM python:3.12.10-slim-bullseye AS builder

# for logging
ARG TARGETPLATFORM
ARG BUILDPLATFORM
RUN echo "🔧 Building for $TARGETPLATFORM on $BUILDPLATFORM"

# Build-time packages – remove later
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git build-essential libffi-dev tzdata && \
    rm -rf /var/lib/apt/lists/*

# Virtual-env with everything pre-built for TARGETPLATFORM
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
FROM python:3.12.10-slim-bullseye

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /processor /processor

ENV PATH="/opt/venv/bin:${PATH}"

RUN mkdir /job
EXPOSE 6000 7001

ENTRYPOINT ["simaas-cli","--log-console","run",\
            "--job-path","/job",\
            "--proc-path","/processor",\
            "--service-address","tcp://0.0.0.0:6000"]
