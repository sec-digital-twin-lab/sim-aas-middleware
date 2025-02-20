import asyncio
import json
import os
import socket
import threading
import time

import traceback
from threading import Lock
from typing import Optional, List, Tuple, Dict

from fastapi import Request

from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy_json import NestedMutableJson

from simaas.cli.cmd_proc_builder import clone_repository, build_processor_image
from simaas.cli.cmd_rti import shorten_id
from simaas.core.helpers import generate_random_string, get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject
from simaas.helpers import docker_find_image, docker_load_image, docker_delete_image, docker_run_job_container, \
    docker_kill_job_container, docker_container_running
from simaas.p2p.base import P2PAddress
from simaas.p2p.protocol import P2PLatency
from simaas.rti.api import RTIService
from simaas.rti.exceptions import RTIException, ProcessorNotDeployedError
from simaas.rti.protocol import P2PInterruptJob, P2PRunnerPerformHandshake, P2PPushJob
from simaas.rti.schemas import Processor, Job, Task, JobStatus
from simaas.dor.schemas import GitProcessorPointer, DataObject, ProcessorDescriptor

logger = Logging.get('rti.service')

Base = declarative_base()


class DBDeployedProcessor(Base):
    __tablename__ = 'deployed_processor'
    id = Column(String(64), primary_key=True)
    state = Column(String, nullable=False)
    image_name = Column(String, nullable=True)
    gpp = Column(NestedMutableJson, nullable=True)
    error = Column(String, nullable=True)


class DBJobInfo(Base):
    __tablename__ = 'job_info'
    id = Column(String(64), primary_key=True)
    proc_id = Column(String(64), nullable=False)
    user_iid = Column(String(64), nullable=False)
    p2p_address_pub = Column(String(64), nullable=False)
    p2p_address_sec = Column(String(64), nullable=False)
    status = Column(NestedMutableJson, nullable=False)
    job = Column(NestedMutableJson, nullable=False)
    container_id = Column(String(16), nullable=False)
    peer = Column(NestedMutableJson, nullable=True)


class DefaultRTIService(RTIService):
    def __init__(self, node, db_path: str, retain_job_history: bool = False, strict_deployment: bool = True):
        super().__init__(retain_job_history=retain_job_history, strict_deployment=strict_deployment)

        # initialise properties
        self._mutex = Lock()
        self._node = node
        self._port_range = (6000, 9000)
        self._most_recent_port = None

        # initialise directories
        self._jobs_path = os.path.join(self._node.datastore, 'jobs')
        self._procs_path = os.path.join(self._node.datastore, 'procs')
        logger.info(f"[init] using jobs path at {self._jobs_path}")
        logger.info(f"[init] using procs path at {self._procs_path}")
        os.makedirs(self._jobs_path, exist_ok=True)
        os.makedirs(self._procs_path, exist_ok=True)

        # initialise database things
        logger.info(f"[init] using database at {db_path}")
        self._engine = create_engine(db_path)
        Base.metadata.create_all(self._engine)
        self._session_maker = sessionmaker(bind=self._engine)

    def _perform_deployment(self, proc: Processor) -> None:
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

    def _perform_undeployment(self, proc_id: str, image_name: str, keep_image: bool = True) -> None:
        # remove the record from the db
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBDeployedProcessor).get(proc_id)
                if record:
                    session.delete(record)
                    session.commit()
                else:
                    logger.warning(f"[undeploy:{shorten_id(proc_id)}] db record not found for removal.")

        # remove the docker image (if applicable)
        if not keep_image:
            try:
                docker_delete_image(image_name)

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                logger.error(f"[undeploy:{shorten_id(proc_id)}] failed to delete docker image {image_name}: {trace}")

    def _perform_job_initialise(self, p2p_address_pub: str, p2p_address_sec: str, job: Job, container_id: str) -> None:
        try:
            custodian = self._node

            logger.info(f"[job:{job.id}] BEGIN initialising job...")

            # wait until the socket can be reached
            latency, attempt = asyncio.run(P2PLatency.perform_unsecured(p2p_address_pub))
            logger.info(f"[job:{job.id}] needed {attempt} attempts to test latency: {latency} msec")

            # perform the handshake
            response: Tuple[GitProcessorPointer, Identity] = asyncio.run(P2PRunnerPerformHandshake.perform(
                p2p_address_pub, custodian.keystore, custodian.p2p.address()
            ))
            peer: Identity = response[1]

            # update the peer information in the database
            with self._session_maker() as session:
                record = session.query(DBJobInfo).get(job.id)
                record.peer = peer.model_dump()
                session.commit()

            # create P2P address
            peer_address = P2PAddress(
                address=p2p_address_sec,
                curve_secret_key=custodian.keystore.curve_secret_key(),
                curve_public_key=custodian.keystore.curve_public_key(),
                curve_server_key=peer.c_public_key
            )

            # wait until the socket can be reached
            latency, attempt = asyncio.run(P2PLatency.perform(peer_address))
            logger.info(f"[job:{job.id}] needed {attempt} attempts to test latency: {latency} msec")

            # push the job to the runner -> this will trigger execution
            asyncio.run(P2PPushJob.perform(peer_address, job))

            logger.info(f"[job:{job.id}] END initialising job -> OK")

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.info(f"[job:{job.id}] END initialising job -> FAILED: {trace}")

            # something went wrong, kill the container to avoid zombies
            docker_kill_job_container(container_id)


    def _find_available_addresses(self, n: int = 2, max_attempts: int = 100) -> List[Tuple[str, int]]:
        def is_port_free(_address: Tuple[str, int]) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)  # Set timeout to avoid blocking indefinitely
                try:
                    sock.connect(_address)
                    return False  # Connection succeeded, port is in use
                except (socket.timeout, ConnectionRefusedError):
                    return True  # Port is free

        host: str = self._node.info.rest_address[0]
        addresses: List[Tuple[str, int]] = []
        for i in range(max_attempts):
            # update the most recent port
            with self._mutex:
                self._most_recent_port = self._most_recent_port + n if self._most_recent_port else self._port_range[0]
                if self._most_recent_port >= self._port_range[1]:
                    self._most_recent_port = self._port_range[0]

                # create the addresses
                for j in range(n):
                    addresses.append((host, self._most_recent_port + j))

            # test if all addresses are available
            all_available = True
            for address in addresses:
                if not is_port_free(address):
                    all_available = False
                    break

            if all_available:
                return addresses

        raise RuntimeError("No free ports found in the specified range.")

    def is_deployed(self, proc_id: str) -> bool:
        with self._session_maker() as session:
            record = session.query(DBDeployedProcessor).get(proc_id)
            return record is not None

    def get_all_procs(self) -> List[Processor]:
        """
        Retrieves a dict of all deployed processors by their id.
        """
        with self._session_maker() as session:
            records = session.query(DBDeployedProcessor).all()
            result = []
            for record in records:
                result.append(Processor(id=record.id, state=Processor.State(record.state),
                                        image_name=record.image_name,
                                        gpp=GitProcessorPointer.model_validate(record.gpp) if record.gpp else None,
                                        error=record.error))

            return result

    def get_proc(self, proc_id: str) -> Optional[Processor]:
        """
        Retrieves a specific processors given its id.
        """
        with self._session_maker() as session:
            record = session.query(DBDeployedProcessor).get(proc_id)
            if record:
                return Processor(id=record.id, state=Processor.State(record.state),
                                 image_name=record.image_name,
                                 gpp=GitProcessorPointer.model_validate(record.gpp) if record.gpp else None,
                                 error=record.error)
            else:
                return None

    def deploy(self, proc_id: str) -> Processor:
        """
        Deploys a processor.
        """

        # is the processor already deployed?
        proc = self.get_proc(proc_id)
        if proc is not None:
            return proc

        # begin deployment
        with self._mutex:
            # create a placeholder processor object
            proc = Processor(id=proc_id, state=Processor.State.BUSY_DEPLOY, image_name=None, gpp=None, error=None)

            # update or create db record
            with self._session_maker() as session:
                record = session.query(DBDeployedProcessor).get(proc.id)
                if record:
                    record.state = proc.state.value
                    record.image_name = proc.image_name
                    record.gpp = proc.gpp.model_dump()
                    record.error = proc.error

                else:
                    session.add(DBDeployedProcessor(id=proc.id, state=proc.state.value, image_name=proc.image_name,
                                                    gpp=proc.gpp.model_dump() if proc.gpp else None, error=None))
                session.commit()

            # start the deployment worker
            threading.Thread(target=self._perform_deployment, args=(proc,)).start()

            return proc

    def undeploy(self, proc_id: str) -> Optional[Processor]:
        """
        Removes a processor from the RTI (if it exists).
        """
        with self._mutex:
            with self._session_maker() as session:
                # do we have a db record for this processor?
                record = session.query(DBDeployedProcessor).get(proc_id)
                if not record:
                    return None

                # create the processor object
                proc = Processor(id=record.id, state=Processor.State(record.state),
                                 image_name=record.image_name,
                                 gpp=GitProcessorPointer.model_validate(record.gpp) if record.gpp else None,
                                 error=record.error)

                # is the state failed? -> delete the db record
                if proc.state == Processor.State.FAILED:
                    logger.warning(f"[undeploy:{shorten_id(proc_id)}] processor failed -> removing it. "
                                   f"error: {record.error}")
                    session.delete(record)
                    session.commit()

                # is the state ready? -> update state to 'busy' and begin undeployment
                elif proc.state == Processor.State.READY:
                    # update the state to busy
                    proc.state = Processor.State.BUSY_UNDEPLOY
                    record.state = proc.state.value
                    session.commit()

                    # start the worker
                    threading.Thread(target=self._perform_undeployment, args=(proc.id, proc.image_name,)).start()

                # is the state busy going up? -> throw error
                elif proc.state == Processor.State.BUSY_DEPLOY:
                    raise RTIException("Cannot undeploy a processor that is currently deploying. Try again later.")

                # is the state busy going down? -> do nothing
                elif proc.state == Processor.State.BUSY_DEPLOY:
                    logger.warning(f"[undeploy:{shorten_id(proc_id)}] already undeploying.")

                return proc

    def submit(self, proc_id: str, task: Task, request: Request) -> Job:
        """
        Submits a task to a deployed processor, thereby creating a new job. Authorisation is required by the owner
        of the task/job.
        """

        # get the user's identity and check if it's identical with that's indicated in the task
        iid = request.headers['saasauth-iid']
        if iid != task.user_iid:
            raise RTIException("Mismatch between user indicated in task and user making request", details={
                'iid': iid,
                'task': task
            })
        user: Identity = self._node.db.get_identity(iid)

        # get the processor
        proc = self.get_proc(task.proc_id)
        if proc is None:
            raise ProcessorNotDeployedError({
                'proc_id': proc_id
            })

        # create the job folder with a generated job id
        job_id = generate_random_string(8)
        job_path = os.path.join(self._jobs_path, job_id)
        os.makedirs(job_path, exist_ok=True)

        # create the initial job descriptor and write to file
        job = Job(id=job_id, task=task, retain=self._retain_job_history, custodian=self._node.info,
                  proc_name=proc.gpp.proc_descriptor.name, t_submitted=get_timestamp_now())
        descriptor_path = os.path.join(job_path, 'job.descriptor')
        with open(descriptor_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump(job.model_dump(), f, indent=2)

        # determine P2P addresses
        p2p_addresses = self._find_available_addresses(n=2)
        p2p_address_pub: Tuple[str, int] = p2p_addresses[0]
        p2p_address_sec: Tuple[str, int] = p2p_addresses[1]

        # create initial job status and write to file
        status = JobStatus(state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={},
                           errors=[], message=None)
        status_path = os.path.join(job_path, 'job.status')
        with open(status_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump(status.model_dump(), f, indent=2)

        # start the job container and keep the container id
        logger.info(f"[submit:{shorten_id(proc_id)}] [job:{job.id}] start job container")
        container_id = docker_run_job_container(proc.image_name, p2p_address_pub, p2p_address_sec)

        p2p_address_pub: str = f"tcp://{p2p_address_pub[0]}:{p2p_address_pub[1]}"
        p2p_address_sec: str = f"tcp://{p2p_address_sec[0]}:{p2p_address_sec[1]}"

        with self._session_maker() as session:
            # create initial job info record and write to database
            record = DBJobInfo(id=job.id, proc_id=proc_id, user_iid=user.id,
                               p2p_address_pub=p2p_address_pub,
                               p2p_address_sec=p2p_address_sec,
                               status=status.model_dump(), job=job.model_dump(),
                               container_id=container_id, peer=None)
            session.add(record)
            session.commit()

        # start the worker
        threading.Thread(
            target=self._perform_job_initialise,
            args=(p2p_address_pub, p2p_address_sec, job, container_id)
        ).start()

        return job

    def jobs_by_proc(self, proc_id: str) -> List[Job]:
        """
        Retrieves a list of active jobs processed by a processor. Any job that is pending execution or actively
        executed will be included in the list.
        """
        # get the records
        with self._mutex:
            with self._session_maker() as session:
                records = session.query(DBJobInfo).filter_by(proc_id=proc_id).all()

        # parse the records
        result: List[Job] = []
        for record in records:
            status = JobStatus.model_validate(record.status)
            if status.state in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED, JobStatus.State.RUNNING]:
                job = Job.model_validate(record.job)
                result.append(job)

        return result

    def jobs_by_user(self, request: Request) -> List[Job]:
        """
        Retrieves a list of active jobs by a user. If the user is the node owner, all active jobs will be returned.
        """
        # get the records
        user: Identity = self._node.db.get_identity(request.headers['saasauth-iid'])
        with self._mutex:
            with self._session_maker() as session:
                if self._node.identity.id == user.id:
                    records = session.query(DBJobInfo).all()
                else:
                    records = session.query(DBJobInfo).filter_by(user_iid=user.id).all()

        # any time period provided?
        result: List[Job] = []
        if 'period' in request.query_params:
            # collect all jobs within the time period
            cutoff = get_timestamp_now() - int(request.query_params['period']) * 3600 * 1000
            for record in records:
                # within time period?
                job = Job.model_validate(record.job)
                if job.t_submitted > cutoff:
                    result.append(job)

        else:
            # collect ony active jobs
            for record in records:
                status = JobStatus.model_validate(record.status)
                if status.state in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                                    JobStatus.State.RUNNING]:
                    job = Job.model_validate(record.job)
                    result.append(job)

        return result

    def update_job_status(self, job_id: str, job_status: JobStatus) -> None:
        """
        Updates the status of a particular job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """
        with self._mutex:
            with self._session_maker() as session:
                # get the record
                record = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

                # TODO: might need to think about restricting status updates from jobs that are marked as finished.
                # # check the status
                # status = JobStatus.model_validate(record.status)
                # if status.state not in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                #                         JobStatus.State.RUNNING]:
                #     logger.warning(f"Job {job_id} is not active -> status cannot be updated.")

                # update the status
                record.status = job_status.model_dump()
                session.commit()

                # update the local status file
                try:
                    status_path = os.path.join(self._jobs_path, job_id, 'job.status')
                    with open(status_path, 'w') as f:
                        # noinspection PyTypeChecker
                        json.dump(job_status.model_dump(), f, indent=2)
                except Exception:
                    logger.warning(f"Could not write job status to {status_path}")

    def get_job_owner_iid(self, job_id: str) -> str:
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")
                return record.user_iid

    def get_job_status(self, job_id: str) -> JobStatus:
        """
        Retrieves detailed information about the status of a job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """
        # get the record
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

        return JobStatus.model_validate(record.status)

    def _perform_cancel(self, job_id: str, peer_address: P2PAddress, grace_period: int = 30) -> None:
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
            container_id = record.container_id

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

    def job_cancel(self, job_id: str) -> JobStatus:
        """
        Attempts to cancel a running job. Depending on the implementation of the processor, this may or may not be
        possible.
        """
        # get the record
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

        # check the status
        status = JobStatus.model_validate(record.status)
        if status.state not in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED, JobStatus.State.RUNNING]:
            raise RTIException(f"Job {job_id} is not active -> job cannot be cancelled.")

        # start the cancellation worker
        peer = Identity.model_validate(record.peer)
        peer_address = P2PAddress(
            address=record.p2p_address_sec,
            curve_secret_key=self._node.keystore.curve_secret_key(),
            curve_public_key=self._node.keystore.curve_public_key(),
            curve_server_key=peer.c_public_key
        )
        threading.Thread(target=self._perform_cancel, args=(job_id, peer_address)).start()

        return status

    def job_purge(self, job_id: str) -> JobStatus:
        """
        Purges a running job. It will be removed regardless of its state.
        """
        # remove the job from database
        with self._mutex:
            with self._session_maker() as session:
                # get the record
                record: Optional[DBJobInfo] = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

                # try to kill the container (if anything is left)
                try:
                    docker_kill_job_container(record.container_id)
                except Exception as e:
                    trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                    logger.warning(f"[job:{job_id}] killing Docker container {record.container_id} failed: {trace}")

                # delete the record
                session.delete(record)
                session.commit()

                status = JobStatus.model_validate(record.status)
                return status
