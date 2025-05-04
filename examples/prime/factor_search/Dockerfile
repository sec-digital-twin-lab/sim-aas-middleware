FROM ubuntu:24.04

RUN apt update -y && \
    apt install -y tzdata && \
    apt install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt update -y && \
    apt install -y python3.12 python3-pip python3.12-venv git

RUN --mount=type=secret,id=git_credentials \
    if [ -f /run/secrets/git_credentials ]; then \
        GIT_CREDENTIALS=$(cat /run/secrets/git_credentials); \
        git clone https://$GIT_CREDENTIALS@github.com/sec-digital-twin-lab/sim-aas-middleware; \
    else \
        git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware; \
    fi

WORKDIR /sim-aas-middleware
RUN git checkout 33e81110431b5a088643f44ac0d846f080f0699b

WORKDIR /processor
COPY . .
RUN python3.12 -m venv /venv
RUN /venv/bin/pip install --upgrade pip setuptools
RUN /venv/bin/pip install -r ./requirements.txt
RUN /venv/bin/pip install /sim-aas-middleware

RUN mkdir /job

EXPOSE 6000

ENTRYPOINT ["/venv/bin/simaas-cli", "--log-console", "run", "--job-path", "/job", "--proc-path", "/processor", "--service-address", "tcp://0.0.0.0:6000"]
