FROM ubuntu:22.04
RUN apt update -y
RUN apt upgrade -y
RUN apt install -y tzdata
RUN apt install software-properties-common -y
RUN add-apt-repository ppa:deadsnakes/ppa
RUN apt update -y
RUN apt install -y python3.10 python3.10-distutils python3-pip git

RUN git clone https://github.com/cooling-singapore/saas-middleware /saas-middleware
WORKDIR /saas-middleware
RUN git checkout 101fd640f3924e4786bea6c1adfaeb3fdd7c2961

WORKDIR /processor
COPY . .
RUN python3.10 -m pip install -r ./requirements.txt
RUN python3.10 -m pip install /saas-middleware

EXPOSE 5000

ENTRYPOINT ["saas-cli", "run", "--job-path", "/job", "--proc-path", "/processor", "--rest-address", "0.0.0.0:5000"]
