import asyncio
import base64
import json
import os
import subprocess
import time

import traceback
from typing import Optional, Tuple, Dict, List

import boto3
from pydantic import BaseModel

from simaas.cli.cmd_image import clone_repository, build_processor_image
from simaas.core.helpers import get_timestamp_now

from simaas.cli.cmd_rti import shorten_id
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject
from simaas.helpers import docker_load_image, docker_delete_image, docker_find_image, docker_check_image_platform, \
    docker_get_exposed_ports
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.p2p.base import P2PAddress
from simaas.rti.base import RTIServiceBase, DBJobInfo, DBDeployedProcessor
from simaas.rti.exceptions import RTIException
from simaas.rti.protocol import P2PInterruptJob
from simaas.rti.schemas import Processor, Job, JobStatus
from simaas.dor.schemas import GitProcessorPointer, DataObject, ProcessorDescriptor

logger = Logging.get('rti.service')


class AWSConfiguration(BaseModel):
    aws_region: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_role_arn: str


REQUIRED_ENV = [
    'SIMAAS_AWS_REGION', 'SIMAAS_AWS_ACCESS_KEY_ID', 'SIMAAS_AWS_SECRET_ACCESS_KEY', 'SIMAAS_AWS_ROLE_ARN',
    'SIMAAS_AWS_JOB_QUEUE', 'SIMAAS_REPO_PATH'
]

def get_default_aws_config() -> Optional[AWSConfiguration]:
    required = ['SIMAAS_AWS_REGION', 'SIMAAS_AWS_ACCESS_KEY_ID', 'SIMAAS_AWS_SECRET_ACCESS_KEY', 'SIMAAS_AWS_ROLE_ARN']
    if all(var in os.environ for var in required):
        return AWSConfiguration(
            aws_region=os.environ['SIMAAS_AWS_REGION'],
            aws_access_key_id=os.environ['SIMAAS_AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['SIMAAS_AWS_SECRET_ACCESS_KEY'],
            aws_role_arn=os.environ['SIMAAS_AWS_ROLE_ARN']
        )
    else:
        return None


def get_ecr_tag(image_name: str) -> str:
    tag: str = image_name
    tag = tag.replace('/', '_')
    tag = tag.replace(':', '_')
    return tag


def get_ecr_image_name(repository_uri: str, image_name: str) -> str:
    return f"{repository_uri}:{image_name}"


def get_ecr_client(config: Optional[AWSConfiguration] = None) -> Tuple[boto3.client, AWSConfiguration]:
    # check if there is a config
    config = config if config else get_default_aws_config()
    if config is None:
        raise RTIException("No AWS configuration found")

    return boto3.client(
        "ecr", region_name=config.aws_region, aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key
    ), config


def get_ecr_repository(repository_name: str, config: Optional[AWSConfiguration] = None) -> str:
    # get the client
    client, config = get_ecr_client(config)

    # check if repository exists
    try:
        response = client.describe_repositories(repositoryNames=[repository_name])
    except client.exceptions.RepositoryNotFoundException:
        print(f"Repository '{repository_name}' does not exist in region '{config.aws_region}' -> creating now.")
        response = client.create_repository(repositoryName=repository_name)

    # extract the repository URI
    repository_uri = response['repositories'][0]['repositoryUri']
    return repository_uri


def ecr_check_image_exists(repository_name: str, image_name: str, config: Optional[AWSConfiguration] = None) -> bool:
    # get the client
    client, config = get_ecr_client(config)

    # ensure the repository exists
    get_ecr_repository(repository_name, config)

    # check if the image exists
    try:
        response = client.describe_images(
            repositoryName=repository_name,
            imageIds=[{"imageTag": get_ecr_tag(image_name)}],
        )
        if response["imageDetails"]:
            return True
    except client.exceptions.ImageNotFoundException:
        return False


def ecr_push_local_image(repository_name: str, image_name: str, config: Optional[AWSConfiguration] = None) -> str:
    # check if the image is 'linux/amd64'
    if not docker_check_image_platform(image_name, 'linux/amd64'):
        raise RTIException(f"Image {image_name} platform not 'linux/amd64'")

    # get the client
    client, config = get_ecr_client(config)

    # ensure the repository exists
    repository_uri = get_ecr_repository(repository_name, config)

    # Authenticate Docker to ECR
    auth_data = client.get_authorization_token()["authorizationData"][0]
    ecr_url = repository_uri.split("/")[0]
    auth_token = base64.b64decode(auth_data["authorizationToken"]).decode()  # Decode base64 token
    username, password = auth_token.split(":")

    # Perform ECR login
    login_cmd = f"echo {password} | docker login --username AWS --password-stdin {ecr_url}"
    result = subprocess.run(login_cmd, shell=True, capture_output=True)
    if result.returncode != 0:
        raise RTIException("Failed to log into ECR", details={
            'stdout': result.stdout.decode('utf-8'),
            'stderr': result.stderr.decode('utf-8')
        })

    # Tag the local image for ECR
    # docker tag <local-image> <aws-account-id>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>
    ecr_image = get_ecr_image_name(repository_uri, get_ecr_tag(image_name))
    result = subprocess.run(f"docker tag {image_name} {ecr_image}", shell=True, capture_output=True)
    if result.returncode != 0:
        raise RTIException("Failed to tag image", details={
            'stdout': result.stdout.decode('utf-8'),
            'stderr': result.stderr.decode('utf-8')
        })

    # Push the image to ECR
    # docker push <aws-account-id>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>
    result = subprocess.run(f"docker push --platform linux/amd64 {ecr_image}", shell=True, capture_output=True)
    if result.returncode != 0:
        raise RTIException("Failed to push to ECR", details={
            'stdout': result.stdout.decode('utf-8'),
            'stderr': result.stderr.decode('utf-8')
        })

    return ecr_image


def ecr_delete_image(repository_name: str, image_name: str, config: Optional[AWSConfiguration] = None):
    # get the client
    client, config = get_ecr_client(config)

    try:
        # Call the batchDeleteImage API to delete the image
        response = client.batch_delete_image(
            repositoryName=repository_name,
            imageIds=[{"imageTag": get_ecr_tag(image_name)}]
        )
        deleted_images = response.get("imageIds", [])
        failures = response.get("failures", [])

        # Handle success and failures
        if deleted_images:
            for image in deleted_images:
                print(f"Deleted image: {image}")
        if failures:
            for failure in failures:
                print(f"Failed to delete image: {failure['imageId']} - Reason: {failure['failureReason']}")
    except Exception as e:
        print(f"Error deleting image: {e}")


def get_batch_client(config: Optional[AWSConfiguration] = None) -> boto3.client:
    # check if there is a config
    config = config if config else get_default_aws_config()
    if config is None:
        raise RTIException("No AWS configuration found")

    return boto3.client(
        "batch", region_name=config.aws_region, aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key
    ), config


def batch_ensure_job_def(
        repository_name: str, proc: Processor, config: Optional[AWSConfiguration] = None
) -> str:
    # get the client
    client, _ = get_batch_client(config)

    # ensure the repository exists
    repository_uri = get_ecr_repository(repository_name, config)

    # determine ECR image name
    ecr_image_name = get_ecr_image_name(repository_uri, get_ecr_tag(proc.image_name))

    # check if the job definition exists
    name = f"job-def_{proc.id}"
    response = client.describe_job_definitions(jobDefinitionName=name, status="ACTIVE")
    if response["jobDefinitions"]:
        return response["jobDefinitions"][0]["jobDefinitionArn"]

    # --- Build volumes list for AWS Batch job definition ---
    aws_volumes = []
    mount_points = []
    for v in proc.volumes:
        # ignore non-EFS volumes
        if "efsFileSystemId" not in v.reference:
            logger.warning(f"Ignoring non-EFS volume {v.name} for {proc.image_name}: {v}")
            continue

        aws_volumes.append({
            "name": v.name,
            "efsVolumeConfiguration": {
                "fileSystemId": v.reference["efsFileSystemId"],
                "rootDirectory": v.reference.get("rootDirectory", "/"),
                "transitEncryption": v.reference.get("transitEncryption", "ENABLED")
            }
        })

        mount_points.append({
            "sourceVolume": v.name,
            "containerPath": v.mount_point,
            "readOnly": v.read_only
        })

    # --- Register job definition ---
    response = client.register_job_definition(
        jobDefinitionName=name,
        type="container",
        containerProperties={
            "image": ecr_image_name,
            "resourceRequirements": [
                {"type": "VCPU", "value": "1"},
                {"type": "MEMORY", "value": "2048"}
            ],
            "networkConfiguration": {
                "assignPublicIp": "ENABLED"  # TODO: without public IP the job won't have internet access
            },
            "fargatePlatformConfiguration": {
                "platformVersion": "LATEST"
            },
            "executionRoleArn": config.aws_role_arn,
            "volumes": aws_volumes,
            "mountPoints": mount_points
        },
        platformCapabilities=["FARGATE"]
    )

    return response['jobDefinitionArn']


def batch_deregister_job_def(proc: Processor, config: Optional[AWSConfiguration] = None) -> None:
    # get the client
    client, _ = get_batch_client(config)

    # check if the job definition exists
    name = f"job-def_{proc.id}"
    response = client.describe_job_definitions(jobDefinitionName=name, status="ACTIVE")
    if response["jobDefinitions"]:
        for job_def in response["jobDefinitions"]:
            job_def_arn = job_def["jobDefinitionArn"]
            client.deregister_job_definition(jobDefinition=job_def_arn)


def batch_run_job(
        repository_name: str, proc: Processor, custodian_address: str, custodian_pubkey: str, job_id: str,
        budget: ResourceDescriptor, config: Optional[AWSConfiguration] = None
) -> str:
    # get the client
    client, config = get_batch_client(config)

    # ensure we have a job definition
    job_def_arn = batch_ensure_job_def(repository_name, proc, config=config)

    response = client.submit_job(
        jobName=job_id,
        jobQueue=os.environ.get('SIMAAS_AWS_JOB_QUEUE', ''),
        jobDefinition=job_def_arn,
        containerOverrides={
            "environment": [
                {"name": "SIMAAS_CUSTODIAN_ADDRESS", "value": custodian_address},
                {"name": "SIMAAS_CUSTODIAN_PUBKEY", "value": custodian_pubkey},
                {"name": "JOB_ID", "value": job_id},
                {"name": "EXTERNAL_P2P_ADDRESS", "value": 'HOSTNAME'},
            ],
            "resourceRequirements": [
                {"type": "VCPU", "value": str(budget.vcpus)},  # vCPUs as string
                {"type": "MEMORY", "value": str(budget.memory)}  # Memory as string (e.g., "1024")
            ]
        }
    )

    return response["jobId"]


def batch_job_running(job_id: str, config: Optional[AWSConfiguration] = None) -> bool:
    # get the client
    client, config = get_batch_client(config)

    response = client.describe_jobs(jobs=[job_id])

    if not response["jobs"]:
        return False  # Job not found

    job_status = response["jobs"][0]["status"]
    return job_status in {"RUNNING", "STARTING", "SUBMITTED", "PENDING"}


def batch_terminate_job(
        job_id: str, config: Optional[AWSConfiguration] = None, reason: str = "Job manually terminated"
) -> None:
    # get the client
    client, config = get_batch_client(config)

    client.terminate_job(jobId=job_id, reason=reason)


class AWSRTIService(RTIServiceBase):
    def __init__(self, node, db_path: str, retain_job_history: bool = False, strict_deployment: bool = True,
                 aws_config: Optional[AWSConfiguration] = None):
        super().__init__(
            node=node, db_path=db_path, retain_job_history=retain_job_history, strict_deployment=strict_deployment
        )

        # check if all required env variables are available
        if not all(var in os.environ for var in REQUIRED_ENV):
            raise RTIException(f"Missing/incomplete AWS configuration. The following environment variables "
                               f"need to be defined: {REQUIRED_ENV}.")

        # determine AWS parameters
        self._aws_repository_name = 'simaas-processors'
        self._aws_config = aws_config if aws_config else get_default_aws_config()
        if self._aws_config is None:
            raise RTIException("No AWS configuration found.")

        # initialise directories
        self._jobs_path = os.path.join(self._node.datastore, 'jobs')
        self._procs_path = os.path.join(self._node.datastore, 'procs')
        logger.info(f"[init] using jobs path at {self._jobs_path}")
        logger.info(f"[init] using procs path at {self._procs_path}")
        os.makedirs(self._jobs_path, exist_ok=True)
        os.makedirs(self._procs_path, exist_ok=True)

    @classmethod
    def plugin_name(cls) -> str:
        return 'aws'

    def type(self) -> str:
        return 'aws'

    def perform_deploy(self, proc: Processor) -> None:
        loop = asyncio.new_event_loop()
        try:
            # search the network for the processor docker image data object and fetch it
            logger.info(f"[deploy][{proc.id}] searching network for PDI: image_name={proc.image_name}")
            protocol = P2PLookupDataObject(self._node)
            custodian = None
            proc_obj: Optional[DataObject] = None
            for node in [node for node in self._node.db.get_network() if node.dor_service]:
                result: Dict[str, DataObject] = loop.run_until_complete(protocol.perform(node, [proc.id]))
                proc_obj = result.get(proc.id)
                if proc_obj:
                    custodian = node
                    break

            # not found?
            if proc_obj is None:
                raise RTIException(f"Processor {proc.id} not found in DOR(s).")

            # determine the image name
            image_name = proc_obj.tags.get('image_name')
            if image_name is None:
                raise RTIException("Malformed processor data object -> image_name not found.")

            # do we have the image in Docker?
            image_found = docker_find_image(image_name)

            # if not, can we load it from DOR?
            if not image_found and proc_obj.data_format == 'tar':
                logger.info(f"[deploy][{proc.id}] image not found but PDI exists as 'tar' -> try loading it...")

                # fetch the data object
                image_path = os.path.join(self._procs_path, f"{proc.id}.content")
                meta_path = os.path.join(self._procs_path, f"{proc.id}.meta")
                protocol = P2PFetchDataObject(self._node)
                loop.run_until_complete(
                    protocol.perform(custodian, proc.id, meta_path, image_path)
                )

                # load the image
                docker_load_image(image_path, image_name)
                image_found = True

            # do we require a rebuild?
            if image_found:
                # check if the existing image matches the required platform
                if docker_check_image_platform(image_name, 'linux/amd64'):
                    require_build = False
                    logger.info(f"[deploy][{proc.id}] image found and arch == linux/amd64 -> no build required!")

                else:
                    # remove the existing image before rebuilding
                    docker_delete_image(image_name)
                    require_build = True
                    logger.info(f"[deploy][{proc.id}] image found but arch != linux/amd64 -> build required!")

            else:
                require_build = True
                logger.info(f"[deploy][{proc.id}] image not found -> build required!")


            # if we require a build -> build it now and push to ECR
            if require_build:
                logger.info(f"[deploy][{proc.id}] trying to build image: {image_name}...")

                # do we have credentials for this repo?
                repository = proc_obj.tags['repository']
                credentials: Optional[GithubCredentials] = self._node.keystore.github_credentials.get(repository)
                credentials: Optional[Tuple[str, str]] = \
                    (credentials.login, credentials.personal_access_token) if credentials else None

                # clone the repository and checkout the specified commit
                repo_path = os.path.join(self._procs_path, proc.id)
                commit_id = proc_obj.tags['commit_id']
                clone_repository(repository, repo_path, commit_id=commit_id, credentials=credentials)

                proc_path = proc_obj.tags['proc_path']
                proc_path = os.path.join(repo_path, proc_path)

                # create the GPP descriptor
                gpp: GitProcessorPointer = GitProcessorPointer(
                    repository=proc_obj.tags['repository'],
                    commit_id=proc_obj.tags['commit_id'],
                    proc_path=proc_obj.tags['proc_path'],
                    proc_descriptor=ProcessorDescriptor.model_validate(proc_obj.tags['proc_descriptor'])
                )
                gpp_path = os.path.join(proc_path, 'gpp.json')
                with open(gpp_path, 'w') as f:
                    json.dump(gpp.model_dump(), f, indent=2)

                # build the image
                build_processor_image(
                    proc_path, os.environ['SIMAAS_REPO_PATH'], image_name,
                    credentials=credentials, platform='linux/amd64'
                )

                logger.info(f"[deploy][{proc.id}] image building complete: {image_name}")

            # if it's not in the ECR yet -> push it now
            if ecr_check_image_exists(self._aws_repository_name, image_name, config=self._aws_config):
                logger.info(f"[deploy][{proc.id}] image already exists in ECR: {image_name} -> do nothing.")
            else:
                logger.info(f"[deploy][{proc.id}] image does not exist in ECR: {image_name} -> pushing to ECR now...")
                ecr_push_local_image(self._aws_repository_name, image_name, config=self._aws_config)

            # find out what ports are exposed
            ports: List[Tuple[int, str]] = docker_get_exposed_ports(image_name)

            # update processor object
            proc.state = Processor.State.READY
            proc.image_name = image_name
            proc.ports = ports
            proc.gpp = GitProcessorPointer(
                repository=proc_obj.tags['repository'], commit_id=proc_obj.tags['commit_id'],
                proc_path=proc_obj.tags['proc_path'], proc_descriptor=proc_obj.tags['proc_descriptor']
            )

            # update the db record
            self.update_proc_db(proc)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"[deploy:{shorten_id(proc.id)}] failed: {trace}")

            proc.state = Processor.State.FAILED
            proc.error = str(e)

            # update the db record
            self.update_proc_db(proc)

        finally:
            loop.close()

    def perform_undeploy(self, proc: Processor, keep_local_image: bool = True) -> None:
        try:
            # deregister job definition
            batch_deregister_job_def(proc, config=self._aws_config)

            # remove it from the ECR
            ecr_delete_image(self._aws_repository_name, proc.image_name, config=self._aws_config)

            # remove the docker image (if applicable)
            if not keep_local_image:
                docker_delete_image(proc.image_name)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"[undeploy:{shorten_id(proc.id)}] failed: {trace}")

        # remove the record from the db
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBDeployedProcessor).get(proc.id)
                if record:
                    session.delete(record)
                    session.commit()
                else:
                    logger.warning(f"[undeploy:{shorten_id(proc.id)}] db record not found for removal.")

    def _perform_submit(
            self, job: Job, proc: Processor, submitted: Optional[List[Tuple[Job, str]]] = None,
            volumes: Optional[Dict[str, dict]] = None
    ) -> str:
        # determine the custodian address and curve public key
        custodian_pubkey: str = self._node.identity.c_public_key
        if "SIMAAS_CUSTODIAN_HOST" in os.environ:
            custodian_host: str = os.environ["SIMAAS_CUSTODIAN_HOST"]
            custodian_address: str = f"tcp://{custodian_host}:{self._node.p2p.port()}"

        else:
            custodian_address: str = self._node.p2p.fq_address()

        # submit the job to AWS Batch
        aws_job_id = batch_run_job(
            self._aws_repository_name, proc, custodian_address, custodian_pubkey, job.id, job.task.budget,
            config=self._aws_config
        )

        if submitted:
            # keep information to terminate if necessary
            submitted.append((job, aws_job_id))

        # update the runner information
        with self._session_maker() as session:
            record = session.get(DBJobInfo, job.id)
            record.runner['aws_job_id'] = aws_job_id
            session.commit()

        return aws_job_id

    def perform_submit_single(self, job: Job, proc: Processor) -> None:
        aws_job_id = None
        try:
            aws_job_id = self._perform_submit(job, proc)
            logger.info(f"[submit:single:{shorten_id(proc.id)}] [job:{job.id}] successful -> aws_job_id={aws_job_id}")

        except Exception as e:
            msg = f"[submit:single:{shorten_id(proc.id)}] [job:{job.id}] failed -> "
            logger.error(
                msg + (f"terminating AWS Batch job {aws_job_id}" if aws_job_id else "no AWS Batch job to terminate")
            )
            if aws_job_id:
                batch_terminate_job(aws_job_id, config=self._aws_config, reason=f"Issue during submit_single: {e}")

            raise e

    def perform_submit_batch(self, batch: List[Tuple[Job, JobStatus, Processor]], batch_id: str) -> Dict[str, dict]:
        submitted: List[Tuple[Job, str]] = []
        for job, status, proc in batch:
            try:
                aws_job_id = self._perform_submit(job, proc)
                logger.info(f"[submit:batch:{batch_id}] [proc:{proc.id}:job:{job.id}] successful"
                            f" -> AWS job {aws_job_id}")

            except Exception as e:
                logger.error(f"[submit:batch:{batch_id}] [proc:{proc.id}:job:{job.id}] failed")

                # Something went wrong, kill already existing AWS jobs so there are no zombies from this batch.
                for job, aws_job_id in submitted:
                    logger.info(f"[submit:batch:{batch_id}] [job:{job.id}] kill zombie AWS job {aws_job_id}")
                    batch_terminate_job(aws_job_id, config=self._aws_config, reason=f"Issue during submit_single: {e}")

                raise e

    def perform_cancel(self, job_id: str, peer_address: Optional[P2PAddress], grace_period: int = 30) -> None:
        # get the AWS job id
        with self._session_maker() as session:
            # get the record and status
            record = session.query(DBJobInfo).get(job_id)
            aws_job_id = record.runner['aws_job_id']

        # attempt to cancel the job gracefully (only possible if we have the peer address
        if peer_address is not None:
            try:
                # send the cancellation request
                asyncio.run(P2PInterruptJob.perform(peer_address))

                # determine deadline and wait until then to see if the job has terminated by then
                deadline = get_timestamp_now() + grace_period * 1000
                while get_timestamp_now() < deadline:
                    time.sleep(1)

                    # is the container still running -> if not, all good we are done
                    if not batch_job_running(aws_job_id):
                        self.on_cancellation_worker_done(job_id)
                        return

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                logger.warning(f"[job:{job_id}] attempt to cancel job {job_id} failed: {trace}")

        # if we reach here, graceful cancellation wasn't possible (deadlined reached, no peer address, exception)
        # -> kill container
        try:
            logger.warning(f"[job:{job_id}] cancellation unsuccessful -> killing AWS Batch job {aws_job_id}")
            batch_terminate_job(aws_job_id)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[job:{job_id}] killing AWS Batch job {aws_job_id} failed: {trace}")

        self.on_cancellation_worker_done(job_id)

    def perform_purge(self, record: DBJobInfo) -> None:
        # try to kill the container (if anything is left)
        aws_job_id = record.runner['aws_job_id']
        try:
            batch_terminate_job(aws_job_id)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[job:{record.id}] killing Docker container {aws_job_id} failed: {trace}")

    def perform_job_cleanup(self, job_id: str) -> None:
        ...

    def resolve_port_mapping(self, job_id: str, runner_details: dict) -> dict:
        # update missing port mappings by using the same host as the runner address. this makes some assumptions
        # about external ports being the same as the ones exposed by the Docker image - which seems to be the case
        # for AWS environments.
        ports: Dict[str, Optional[str]] = runner_details['ports']
        ext_host = runner_details['address'].split("://", 1)[-1].split(":", 1)[0]
        for local, external in ports.items():
            if external is None:
                port, protocol = local.split("/")
                ports[local] = f"{protocol}://{ext_host}:{port}"
        return ports

