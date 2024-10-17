FROM ubuntu:24.04

# Setup system
RUN apt update -y && \
    apt install -y tzdata && \
    apt install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt update -y && \
    apt install -y python3.12 python3-pip python3.12-venv git

# Install the Sim-aaS Middleware
RUN --mount=type=secret,id=git_credentials \
    if [ -f /run/secrets/git_credentials ]; then \
        GIT_CREDENTIALS=$(cat /run/secrets/git_credentials); \
        git clone https://$GIT_CREDENTIALS@github.com/sec-digital-twin-lab/sim-aas-middleware; \
    else \
        git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware; \
    fi

WORKDIR /sim-aas-middleware
RUN git checkout 807c83d3d91218bdfd95a8b7b6c1d77c8783adbc

# Install the repository with the processor -> in this example that happens to be the same repository
RUN ln -s /sim-aas-middleware /proc-repo
WORKDIR /proc-repo
RUN python3.12 -m venv venv
RUN ./venv/bin/pip install --upgrade pip setuptools
RUN ./venv/bin/pip install -r ./requirements.txt
RUN ./venv/bin/pip install /sim-aas-middleware

EXPOSE 5000
EXPOSE 6000

ENTRYPOINT ["venv/bin/saas-cli", "run", "--job-path", "/job", "--proc-path", "/proc-repo", "--rest-address", "0.0.0.0:5000", "--p2p-address", "0.0.0.0:6000"]
