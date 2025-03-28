import asyncio
import json
import os
import socket
import time

import traceback
from typing import Optional, Tuple, Dict, List

from simaas.cli.cmd_proc_builder import clone_repository, build_processor_image
from simaas.cli.cmd_rti import shorten_id
from simaas.core.helpers import get_timestamp_now
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject
from simaas.helpers import docker_find_image, docker_load_image, docker_delete_image, docker_run_job_container, \
    docker_kill_job_container, docker_container_running, docker_get_exposed_ports
from simaas.p2p.base import P2PAddress
from simaas.rti.base import RTIServiceBase, DBDeployedProcessor, DBJobInfo
from simaas.rti.exceptions import RTIException
from simaas.rti.protocol import P2PInterruptJob
from simaas.rti.schemas import Processor, Job, JobStatus
from simaas.dor.schemas import GitProcessorPointer, DataObject, ProcessorDescriptor

logger = Logging.get('rti.service')


class DefaultRTIService(RTIServiceBase):
    def __init__(self, node, db_path: str, retain_job_history: bool = False, strict_deployment: bool = True):
        super().__init__(
            node=node, db_path=db_path, retain_job_history=retain_job_history, strict_deployment=strict_deployment
        )

        # initialise properties
        self._port_range = (6000, 9000)
        self._most_recent_port = None

    def perform_deploy(self, proc: Processor) -> None:
        loop = asyncio.new_event_loop()
        try:
            # search the network for the processor docker image data object and fetch it
            protocol = P2PLookupDataObject(self._node)
            custodian = None
            proc_obj = None
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

            # do we already have this docker image deployed? if not fetch and load from DOR
            if not docker_find_image(image_name):
                # is the processor data object and image or a GPP?
                if proc_obj.data_format == 'tar':
                    # fetch the data object
                    meta_path = os.path.join(self._procs_path, f"{proc.id}.meta")
                    content_path = os.path.join(self._procs_path, f"{proc.id}.content")
                    protocol = P2PFetchDataObject(self._node)
                    loop.run_until_complete(
                        protocol.perform(custodian, proc.id, meta_path, content_path)
                    )

                    # load the image
                    image = docker_load_image(content_path, image_name)
                    if image is None:
                        raise RTIException(f"Image loaded but {image_name} not found.")

                else:
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
                    build_processor_image(proc_path, image_name, credentials=credentials)

            # update processor object
            proc.state = Processor.State.READY
            proc.image_name = image_name
            proc.gpp = GitProcessorPointer(
                repository=proc_obj.tags['repository'], commit_id=proc_obj.tags['commit_id'],
                proc_path=proc_obj.tags['proc_path'], proc_descriptor=proc_obj.tags['proc_descriptor']
            )

            # update the db record
            with self._mutex:
                with self._session_maker() as session:
                    record = session.query(DBDeployedProcessor).get(proc.id)
                    if record:
                        record.state = proc.state.value
                        record.image_name = proc.image_name
                        record.gpp = proc.gpp.model_dump()
                        record.error = proc.error

                    else:
                        logger.warning(f"[deploy:{shorten_id(proc.id)}] database record for proc {proc.id}:"
                                       f"{proc.image_name} expected to exist but not found -> creating now.")
                        session.add(DBDeployedProcessor(id=proc.id, state=proc.state.value, image_name=proc.image_name,
                                                        gpp=proc.gpp.model_dump() if proc.gpp else None, error=None))
                    session.commit()

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"[deploy:{shorten_id(proc.id)}] failed: {trace}")

            proc.state = Processor.State.FAILED
            proc.error = str(e)
        finally:
            loop.close()

    def perform_undeploy(self, proc: Processor, keep_image: bool = True) -> None:
        # remove the docker image (if applicable)
        if not keep_image:
            try:
                docker_delete_image(proc.image_name)

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                logger.error(
                    f"[undeploy:{shorten_id(proc.id)}] failed to delete docker image {proc.image_name}: {trace}"
                )

        # remove the record from the db
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBDeployedProcessor).get(proc.id)
                if record:
                    session.delete(record)
                    session.commit()
                else:
                    logger.warning(f"[undeploy:{shorten_id(proc.id)}] db record not found for removal.")

    def perform_submit_single(self, job: Job, proc: Processor) -> None:
        container_id = None
        try:
            # get the runner address and custom port mappings (if any)
            runner_p2p_address, custom_ports = self._map_ports(proc.image_name)

            # start the job container and keep the container id
            container_id = docker_run_job_container(
                proc.image_name, runner_p2p_address, self._node.p2p.address(), self._node.identity.c_public_key, job.id,
                budget=job.task.budget, custom_ports=custom_ports
            )

            # update the runner information
            with self._session_maker() as session:
                record = session.get(DBJobInfo, job.id)
                record.runner['container_id'] = container_id
                session.commit()

            logger.info(f"[submit:single:{shorten_id(proc.id)}] [job:{job.id}] successful -> container {container_id}")

        except Exception as e:
            msg = f"[submit:single:{shorten_id(proc.id)}] [job:{job.id}] failed -> "
            logger.error(msg + (f"terminating {container_id}" if container_id else "no container to terminate"))
            if container_id:
                docker_kill_job_container(container_id)

            raise e

    def perform_submit_batch(self, batch: List[Tuple[Job, JobStatus, Processor]], batch_id: str) -> Dict[str, dict]:
        raise RTIException("Not implemented yet.")

    def perform_cancel(self, job_id: str, peer_address: P2PAddress, grace_period: int = 30) -> None:
        # attempt to cancel the job
        try:
            asyncio.run(P2PInterruptJob.perform(peer_address))

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[job:{job_id}] attempt to cancel job {job_id} failed: {trace}")
            grace_period = 0

        with self._session_maker() as session:
            # get the record and status
            record = session.query(DBJobInfo).get(job_id)
            container_id = record.runner['container_id']

            deadline = get_timestamp_now() + grace_period * 1000
            while get_timestamp_now() < deadline:
                try:
                    # sleep for a bit and check the record
                    time.sleep(1)

                    # is the container still running -> if not, all good we are done
                    if not docker_container_running(container_id):
                        return

                except Exception as e:
                    trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                    logger.warning(f"[job:{job_id}] checking status failed: {trace}")
                    break

            # if we get here then either the deadline has been reached or there was an exception -> kill container
            try:
                logger.warning(f"[job:{job_id}] grace period exceeded -> "
                               f"killing Docker container {container_id}")
                docker_kill_job_container(container_id)

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                logger.warning(f"[job:{job_id}] killing Docker container {container_id} failed: {trace}")

    def perform_purge(self, record: DBJobInfo) -> None:
        # try to kill the container (if anything is left)
        try:
            container_id = record.runner['container_id']
            docker_kill_job_container(container_id)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[job:{record.id}] killing Docker container {record.container_id} failed: {trace}")

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
            with self._mutex:
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

    def _map_ports(self, image_name: str) -> Tuple[Tuple[str, int], List[Tuple[int, str, str, int]]]:
        # create a mapping of ports exposed by the Docket image to P2P addresses
        custom_ports: List[Tuple[int, str, str, int]] = []
        runner_p2p_address: Optional[Tuple[str, int]] = None
        for port, protocol in docker_get_exposed_ports(image_name):
            address = self._find_available_address()
            if port == 6000 and protocol == 'tcp':
                runner_p2p_address = address
            else:
                custom_ports.append((port, protocol, address[0], address[1]))

        # do we have a runner P2P address?
        if runner_p2p_address is None:
            raise RTIException(f"Processor docker image invalid: runner P2P port not exposed")

        return runner_p2p_address, custom_ports
