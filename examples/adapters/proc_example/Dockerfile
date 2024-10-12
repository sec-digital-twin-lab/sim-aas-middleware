FROM ubuntu:22.04
RUN apt update -y
RUN apt upgrade -y
RUN apt install -y tzdata
RUN apt install software-properties-common -y
RUN add-apt-repository ppa:deadsnakes/ppa
RUN apt update -y
RUN apt install -y python3.12 python3.12-distutils python3-pip git

RUN git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware /sim-aas-middleware
WORKDIR /sim-aas-middleware
RUN git checkout 4a077599015dba770de01d497af740fe66b3cda0

WORKDIR /processor
COPY . .
RUN python3.10 -m pip install -r ./requirements.txt
RUN python3.10 -m pip install /sim-aas-middleware

EXPOSE 5000

ENTRYPOINT ["saas-cli", "run", "--job-path", "/job", "--proc-path", "/processor", "--rest-address", "0.0.0.0:5000"]
