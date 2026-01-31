import asyncio
import json
import os
import shutil
import socket
import threading

import traceback
from typing import Optional, Tuple, Dict, List

from simaas.cli.cmd_image import clone_repository, build_processor_image
from simaas.cli.cmd_rti import shorten_id
from simaas.core.helpers import get_timestamp_now
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject
from simaas.helpers import docker_find_image, docker_load_image, docker_delete_image, docker_run_job_container, \
    docker_kill_job_container, docker_container_running, docker_get_exposed_ports, docker_delete_container
from simaas.p2p.base import P2PAddress
from simaas.rti.base import RTIServiceBase, DBDeployedProcessor, DBJobInfo
from simaas.core.errors import ConfigurationError, ValidationError, OperationError, NotFoundError
from simaas.rti.protocol import P2PInterruptJob
from simaas.rti.schemas import Processor, Job, JobStatus, ProcessorVolume
from simaas.dor.schemas import GitProcessorPointer, DataObject, ProcessorDescriptor

logger = Logging.get('rti.service')


REQUIRED_ENV = [
    'SIMAAS_REPO_PATH'
]

class DockerRTIService(RTIServiceBase):
    def __init__(
            self, node, db_path: str, retain_job_history: bool = False, strict_deployment: bool = True
    ) -> None:
        super().__init__(
            node=node, db_path=db_path, retain_job_history=retain_job_history, strict_deployment=strict_deployment
        )

        # check if all required env variables are available
        if not all(var in os.environ for var in REQUIRED_ENV):
            raise ConfigurationError(setting=str(REQUIRED_ENV), hint='environment variables required')

        # initialise properties
        self._port_range = (6000, 9000)
        self._most_recent_port = None
        self._port_lock = threading.Lock()  # sync lock for port allocation
        self._scratch_volume = os.environ.get('SIMAAS_RTI_DOCKER_SCRATCH_PATH')

        # determine the scratch path (if any)
        if self._scratch_volume:
            if os.path.isdir(self._scratch_volume):
                logger.info(f"Docker RTI scratch path at '{self._scratch_volume}' found.")
            else:
                logger.warning(f"Docker RTI scratch path at '{self._scratch_volume}' not found -> skipping")
                self._scratch_volume = None

    @classmethod
    def plugin_name(cls) -> str:
        return 'docker'

    def type(self) -> str:
        return 'docker'

    async def perform_deploy(self, proc: Processor) -> None:
        try:
            # search the network for the processor docker image data object and fetch it
            protocol = P2PLookupDataObject(self._node)
            custodian = None
            proc_obj = None
            for node in [node for node in await self._node.db.get_network() if node.dor_service]:
                result: Dict[str, DataObject] = await protocol.perform(node, [proc.id])
                proc_obj = result.get(proc.id)
                if proc_obj:
                    custodian = node
                    break

            # not found?
            if proc_obj is None:
                raise NotFoundError(resource_type='processor', resource_id=proc.id, hint='not found in DOR(s)')

            # determine the image name
            image_name = proc_obj.tags.get('image_name')
            if image_name is None:
                raise ValidationError(field='tags.image_name', hint='missing from processor data object')

            # do we already have this docker image deployed? if not fetch and load from DOR
            if not await asyncio.to_thread(docker_find_image, image_name):
                # is the processor data object and image or a GPP?
                if proc_obj.data_format == 'tar':
                    # fetch the data object
                    meta_path = os.path.join(self._procs_path, f"{proc.id}.meta")
                    content_path = os.path.join(self._procs_path, f"{proc.id}.content")
                    protocol = P2PFetchDataObject(self._node)
                    await protocol.perform(custodian, proc.id, meta_path, content_path)

                    # load the image
                    image = await asyncio.to_thread(docker_load_image, content_path, image_name)
                    if image is None:
                        raise OperationError(operation='docker_load', stage='verify', cause=f'image {image_name} not found after load')

                else:
                    # do we have credentials for this repo?
                    repository = proc_obj.tags['repository']
                    credentials: Optional[GithubCredentials] = self._node.keystore.github_credentials.get(repository)
                    credentials: Optional[Tuple[str, str]] = \
                        (credentials.login, credentials.personal_access_token) if credentials else None

                    # clone the repository and checkout the specified commit
                    repo_path = os.path.join(self._procs_path, proc.id)
                    commit_id = proc_obj.tags['commit_id']
                    await asyncio.to_thread(
                        clone_repository, repository, repo_path, commit_id=commit_id, credentials=credentials
                    )

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
                    await asyncio.to_thread(
                        build_processor_image,
                        proc_path, os.environ['SIMAAS_REPO_PATH'], image_name, credentials=credentials
                    )

            # find out what ports are exposed
            ports: List[Tuple[int, str]] = await asyncio.to_thread(docker_get_exposed_ports, image_name)

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

    async def perform_undeploy(self, proc: Processor, keep_image: bool = True) -> None:
        # remove the docker image (if applicable)
        if not keep_image:
            try:
                await asyncio.to_thread(docker_delete_image, proc.image_name)

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                logger.error(
                    f"[undeploy:{shorten_id(proc.id)}] failed to delete docker image {proc.image_name}: {trace}"
                )

        # remove the record from the db - no lock needed, session is per-call
        with self._session_maker() as session:
            record = session.get(DBDeployedProcessor, proc.id)
            if record:
                session.delete(record)
                session.commit()
            else:
                logger.warning(f"[undeploy:{shorten_id(proc.id)}] db record not found for removal.")

    def _perform_submit(
            self, job: Job, proc: Processor, submitted: Optional[List[Tuple[Job, str]]] = None
    ) -> str:
        # get the runner address and custom port mappings (if any)
        runner_p2p_address, custom_ports, ports = self._map_ports(proc.ports)

        # get volumes information
        volumes = {}
        for volume in proc.volumes:
            volume = ProcessorVolume.model_validate(volume)
            path = volume.reference['path']
            volumes[path] = {'bind': volume.mount_point, 'mode': 'ro' if volume.read_only else 'rw'}

        # add the scratch volume (if any)
        if self._scratch_volume is not None:
            # create the job-specific scratch folder
            job_scratch_path = os.path.abspath(os.path.join(self._scratch_volume, f"{job.id}_scratch"))
            os.makedirs(job_scratch_path, exist_ok=True)

            # add to the volumes
            volumes[job_scratch_path] = {'bind': '/scratch', 'mode': 'rw'}

        # start the job container and keep the container id
        container_id = docker_run_job_container(
            proc.image_name, runner_p2p_address, self._node.p2p.address(), self._node.identity.c_public_key, job.id,
            budget=job.task.budget, custom_ports=custom_ports, volumes=volumes if volumes else None
        )

        if submitted:
            # keep information to terminate if necessary
            submitted.append((job, container_id))

        # update the runner information
        with self._session_maker() as session:
            record = session.get(DBJobInfo, job.id)
            record.runner['container_id'] = container_id
            record.runner['__ports'] = ports
            session.commit()

        return container_id

    def perform_submit_single(self, job: Job, proc: Processor) -> None:
        container_id = None
        try:
            container_id = self._perform_submit(job, proc)
            logger.info(f"[submit:single:{shorten_id(proc.id)}] [job:{job.id}] successful -> container {container_id}")

        except Exception as e:
            msg = f"[submit:single:{shorten_id(proc.id)}] [job:{job.id}] failed -> "
            logger.error(msg + (f"terminating {container_id}" if container_id else "no container to terminate"))
            if container_id:
                docker_kill_job_container(container_id)

            raise e

    def perform_submit_batch(self, batch: List[Tuple[Job, JobStatus, Processor]], batch_id: str) -> None:
        submitted: List[Tuple[Job, str]] = []
        for job, status, proc in batch:
            try:
                container_id = self._perform_submit(job, proc, submitted=submitted)
                logger.info(f"[submit:batch:{batch_id}] [proc:{proc.id}:job:{job.id}] successful"
                            f" -> container {container_id}")

            except Exception as e:
                logger.error(f"[submit:batch:{batch_id}] [proc:{proc.id}:job:{job.id}] failed")

                # Something went wrong, kill already existing containers so there are no zombies from this batch.
                for job, container_id in submitted:
                    logger.info(f"[submit:batch:{batch_id}] [job:{job.id}] kill zombie container {container_id}")
                    docker_kill_job_container(container_id)

                raise e

    async def perform_cancel(self, job_id: str, peer_address: P2PAddress, grace_period: int = 30) -> None:
        try:
            # mark cancelled in DB first (state is correct even if we crash later)
            self.mark_job_cancelled(job_id)

            # get container_id (quick read, don't hold session)
            with self._session_maker() as session:
                record = session.get(DBJobInfo, job_id)
                container_id = record.runner.get('container_id') if record else None

            if not container_id:
                return

            # send P2P interrupt (best effort)
            if peer_address:
                try:
                    await P2PInterruptJob.perform(peer_address)
                except Exception:
                    pass

            # wait grace period for container to stop
            deadline = get_timestamp_now() + grace_period * 1000
            while get_timestamp_now() < deadline:
                if not await asyncio.to_thread(docker_container_running, container_id):
                    break
                await asyncio.sleep(1)

            # force kill if still running
            if await asyncio.to_thread(docker_container_running, container_id):
                await asyncio.to_thread(docker_kill_job_container, container_id)

            # cleanup (if not retaining history)
            if not self.retain_job_history:
                await asyncio.to_thread(docker_delete_container, container_id)
                if self._scratch_volume:
                    scratch_path = os.path.join(self._scratch_volume, f"{job_id}_scratch")
                    shutil.rmtree(scratch_path, ignore_errors=True)

        except Exception as e:
            logger.error(f"[job:{job_id}] cancellation failed: {e}")

        finally:
            self.on_cancellation_worker_done(job_id)

    async def perform_purge(self, record: DBJobInfo) -> None:
        # try to kill the container (if anything is left)
        try:
            container_id = record.runner['container_id']
            await asyncio.to_thread(docker_kill_job_container, container_id)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[job:{record.id}] killing Docker container {record.runner.get('container_id')} failed: {trace}")

    async def perform_job_cleanup(self, job_id: str) -> None:
        try:
            # only clean up if we are not supposed to keep the job history
            if not self.retain_job_history:
                # delete the container
                with self._session_maker() as session:
                    record: DBJobInfo = session.get(DBJobInfo, job_id)

                    # wait for docker container to be shutdown
                    container_id: str = record.runner['container_id']
                    logger.info(f"[job:{job_id}] clean-up -> waiting for container {container_id} to be stopped")
                    while await asyncio.to_thread(docker_container_running, container_id):
                        await asyncio.sleep(1)

                    # delete the container
                    logger.info(f"[job:{job_id}] clean-up -> delete container {container_id}")
                    await asyncio.to_thread(docker_delete_container, container_id)

                # delete the scratch folder (if any)
                if self._scratch_volume is not None:
                    # create the job-specific scratch folder
                    job_scratch_path = os.path.abspath(os.path.join(self._scratch_volume, f"{job_id}_scratch"))
                    logger.info(f"[job:{job_id}] clean-up -> delete scratch folder at {job_scratch_path}")
                    try:
                        await asyncio.to_thread(shutil.rmtree, job_scratch_path)
                    except OSError as e:
                        logger.warning(f"[job:{job_id}] Failed to delete scratch folder: {e}")

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"[job:{job_id}] clean-up failed: {trace}")

        finally:
            # notify base class that we are done
            self.on_cleanup_worker_done(job_id)

    def resolve_port_mapping(self, job_id: str, runner_details: dict) -> dict:
        # in case the of the default RTI the port mapping is actually already known during submission time.
        # so we just need to extract it here...
        ports_ref: Dict[str, str] = runner_details['__ports']
        ports: Dict[str, Optional[str]] = runner_details['ports']
        for local in ports.keys():
            ref = ports_ref[local]
            ports[local] = ref
        return ports

    def _find_available_address(self, max_attempts: int = 100) -> Tuple[str, int]:
        def is_port_free(_address: Tuple[str, int]) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)  # Set timeout to avoid blocking indefinitely
                try:
                    sock.connect(_address)
                    return False  # Connection succeeded, port is in use
                except (socket.timeout, ConnectionRefusedError):
                    return True  # Port is free

        host: str = self._node.info.rest_address[0]
        for i in range(max_attempts):
            with self._port_lock:
                # update the most recent port
                self._most_recent_port = self._most_recent_port + 1 if self._most_recent_port else self._port_range[0]
                if self._most_recent_port >= self._port_range[1]:
                    self._most_recent_port = self._port_range[0]

                # create the address
                address = (host, self._most_recent_port)

            # test the address
            if is_port_free(address):
                return address

        raise RuntimeError("No free ports found in the specified range.")

    def _map_ports(self, exposed_ports: List[Tuple[int, str]]) -> Tuple[Tuple[str, int], List[Tuple[int, str, str, int]], Dict[str, str]]:
        # create a mapping of ports exposed by the Docket image to P2P addresses
        custom_ports: List[Tuple[int, str, str, int]] = []
        runner_p2p_address: Optional[Tuple[str, int]] = None
        for port, protocol in exposed_ports:
            address = self._find_available_address()
            if port == 6000 and protocol == 'tcp':
                runner_p2p_address = address
            else:
                custom_ports.append((port, protocol, address[0], address[1]))

        # do we have a runner P2P address?
        if runner_p2p_address is None:
            raise ValidationError(field='exposed_ports', hint='runner P2P port 6000/tcp not exposed')

        # create the ports mapping information
        ports: Dict[str, str] = {
            # P2P protocol port is 6000/tcp by convention
            "6000/tcp": f"tcp://{runner_p2p_address[0]}:{runner_p2p_address[1]}",
        }
        for port, protocol, ext_host, ext_port in custom_ports:
            ports[f"{port}/{protocol}"] = f"{protocol}://{ext_host}:{ext_port}"

        return runner_p2p_address, custom_ports, ports
