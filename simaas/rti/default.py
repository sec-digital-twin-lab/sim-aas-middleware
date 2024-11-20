import asyncio
import json
import os
import socket
import threading

import traceback
from threading import Lock
from typing import Optional, List, Tuple, Dict

from fastapi import Request
from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy_json import NestedMutableJson

from simaas.cli.cmd_job_runner import JobRunner
from simaas.cli.cmd_proc_builder import clone_repository, build_processor_image
from simaas.cli.cmd_rti import shorten_id
from simaas.core.helpers import generate_random_string, get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.core.schemas import GithubCredentials
from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject
from simaas.helpers import docker_find_image, docker_load_image, docker_delete_image, docker_run_job_container
from simaas.rti.api import RTIService, JobRESTProxy
from simaas.rti.exceptions import RTIException, ProcessorNotDeployedError
from simaas.rti.schemas import Processor, Job, Task, JobStatus
from simaas.dor.schemas import GitProcessorPointer, DataObject

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
    rest_address = Column(String(64), nullable=False)
    status = Column(NestedMutableJson, nullable=False)
    job = Column(NestedMutableJson, nullable=False)


def run_job_cmd(job_path: str, proc_path: str, proc_name: str, rest_address: str, rti_rest_address: str) -> None:
    try:
        cmd = JobRunner()
        cmd.execute({
            'job_path': job_path,
            'proc_path': proc_path,
            'proc_name': proc_name,
            'rest_address': rest_address,
            'rti_rest_address': rti_rest_address
        })
    except Exception as e:
        logger.error(e)


class DefaultRTIService(RTIService):
    def __init__(self, node, db_path: str, retain_job_history: bool = False, strict_deployment: bool = True,
                 job_concurrency: bool = True):
        super().__init__(retain_job_history=retain_job_history, strict_deployment=strict_deployment,
                         job_concurrency=job_concurrency)

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
        logger.info(f"[init] using DB file at {db_path}")
        self._engine = create_engine(db_path)
        Base.metadata.create_all(self._engine)
        self._session_maker = sessionmaker(bind=self._engine)

    def job_descriptor_path(self, job_id: str) -> str:
        return os.path.join(self._jobs_path, job_id, 'job_descriptor.json')

    def job_status_path(self, job_id: str) -> str:
        return os.path.join(self._jobs_path, job_id, 'job_status.json')

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

                    # build the image
                    proc_path = proc_obj.tags['proc_path']
                    image_name_new, _, _ = build_processor_image(repo_path, proc_path, credentials=credentials)
                    if image_name_new != image_name:
                        raise RTIException(f"Mismatching image names after building from GPP: actual={image_name_new} "
                                           f"!= expected={image_name}")

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
                        record.gpp = proc.gpp.dict()
                        record.error = proc.error

                    else:
                        logger.warning(f"[deploy:{shorten_id(proc.id)}] database record for proc {proc.id}:"
                                       f"{proc.image_name} expected to exist but not found -> creating now.")
                        session.add(DBDeployedProcessor(id=proc.id, state=proc.state.value, image_name=proc.image_name,
                                                        gpp=proc.gpp.dict() if proc.gpp else None, error=None))
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

    def _find_available_job_address(self, max_attempts: int = 100) -> Tuple[str, int]:
        def is_port_free(host: str, port: int) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)  # Set timeout to avoid blocking indefinitely
                try:
                    sock.connect((host, port))
                    return False  # Connection succeeded, port is in use
                except (socket.timeout, ConnectionRefusedError):
                    return True  # Port is free

        host = self._node.info.rest_address[0]
        for i in range(max_attempts):
            # update the most recent port
            with self._mutex:
                self._most_recent_port = self._most_recent_port + 1 if self._most_recent_port else self._port_range[0]
                if self._most_recent_port >= self._port_range[1]:
                    self._most_recent_port = self._port_range[0]
                port = self._most_recent_port

            if is_port_free(host, port):
                return host, port

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
                                        gpp=GitProcessorPointer.parse_obj(record.gpp) if record.gpp else None,
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
                                 gpp=GitProcessorPointer.parse_obj(record.gpp) if record.gpp else None,
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
                    record.gpp = proc.gpp.dict()
                    record.error = proc.error

                else:
                    session.add(DBDeployedProcessor(id=proc.id, state=proc.state.value, image_name=proc.image_name,
                                                    gpp=proc.gpp.dict() if proc.gpp else None, error=None))
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
                                 gpp=GitProcessorPointer.parse_obj(record.gpp) if record.gpp else None,
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
            json.dump(job.dict(), f, indent=2)

        # write the gpp descriptor
        gpp_path = os.path.join(job_path, 'gpp.descriptor')
        with open(gpp_path, 'w') as f:
            json.dump(proc.gpp.dict(), f, indent=2)

        # create the initial job status and write to file
        status = JobStatus(state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={},
                           errors=[], message=None)
        status_path = os.path.join(job_path, 'job.status')
        with open(status_path, 'w') as f:
            json.dump(status.dict(), f, indent=2)

        # determine REST address
        job_address = self._find_available_job_address()

        # write the initial job info database record
        with self._mutex:
            with self._session_maker() as session:
                record = DBJobInfo(id=job.id, proc_id=proc_id, user_iid=user.id,
                                   rest_address=f"{job_address[0]}:{job_address[1]}",
                                   status=status.dict(), job=job.dict())
                session.add(record)
                session.commit()

        # start the job container
        logger.info(f"[submit:{shorten_id(proc_id)}] [job:{job.id}] start job container")
        docker_run_job_container(proc.image_name, job_path, job_address)

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
            status = JobStatus.parse_obj(record.status)
            if status.state in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                                JobStatus.State.PREPROCESSING, JobStatus.State.RUNNING,
                                JobStatus.State.POSTPROCESSING]:
                job = Job.parse_obj(record.job)
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
                job = Job.parse_obj(record.job)
                if job.t_submitted > cutoff:
                    result.append(job)

        else:
            # collect ony active jobs
            for record in records:
                status = JobStatus.parse_obj(record.status)
                if status.state in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                                    JobStatus.State.PREPROCESSING, JobStatus.State.RUNNING,
                                    JobStatus.State.POSTPROCESSING]:
                    job = Job.parse_obj(record.job)
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

                # check the status
                status = JobStatus.parse_obj(record.status)
                if status.state not in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                                        JobStatus.State.PREPROCESSING, JobStatus.State.RUNNING,
                                        JobStatus.State.POSTPROCESSING]:
                    raise RTIException(f"Job {job_id} is not active -> status cannot be updated.")

                # update the status
                record.status = job_status.dict()
                session.commit()

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

        return JobStatus.parse_obj(record.status)

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
        status = JobStatus.parse_obj(record.status)
        if status.state not in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                                JobStatus.State.PREPROCESSING, JobStatus.State.RUNNING,
                                JobStatus.State.POSTPROCESSING]:
            raise RTIException(f"Job {job_id} is not active -> job cannot be cancelled.")

        # attempt to cancel the job
        try:
            rest_address = record.rest_address.split(':')
            rest_address = (rest_address[0], int(rest_address[1]))
            job_proxy = JobRESTProxy(rest_address)
            job_proxy.job_cancel()
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise RTIException(f"Attempt to cancel job {job_id} failed -> {trace}")

        return status

    def job_purge(self, job_id: str) -> JobStatus:
        """
        Purges a running job. It will be removed regardless of its state.
        """
        # get the record
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

        # check the status
        status = JobStatus.parse_obj(record.status)

        # attempt to cancel the job
        try:
            rest_address = record.rest_address.split(':')
            rest_address = (rest_address[0], int(rest_address[1]))
            job_proxy = JobRESTProxy(rest_address)
            job_proxy.job_cancel()
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.warning(f"[job:{job_id}] attempt to cancel failed -> {trace}")

        # remove it from database
        with self._mutex:
            with self._session_maker() as session:
                record = session.query(DBJobInfo).get(job_id)
                if record:
                    session.delete(record)
                    session.commit()
                else:
                    logger.warning(f"[job:{job_id}] db record not found for purging.")

        return status
