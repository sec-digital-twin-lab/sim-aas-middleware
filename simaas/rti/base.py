import abc
import json
import os
import threading
import traceback
from typing import Optional, List, Tuple

from fastapi.requests import Request

from simaas.core.exceptions import ExceptionContent
from simaas.core.helpers import generate_random_string, get_timestamp_now
from simaas.cli.helpers import shorten_id
from simaas.dor.schemas import GitProcessorPointer
from simaas.core.identity import Identity
from simaas.rti.exceptions import RTIException, ProcessorNotDeployedError
from simaas.rti.schemas import JobStatus, Processor, Task, Job, BatchStatus
from simaas.core.logging import Logging
from simaas.p2p.base import P2PAddress
from simaas.rti.api import RTIRESTService

from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy_json import NestedMutableJson

logger = Logging.get('rti.service')


Base = declarative_base()


class DBDeployedProcessor(Base):
    __tablename__ = 'deployed_processor'
    id = Column(String(64), primary_key=True)
    state = Column(String, nullable=False)
    image_name = Column(String, nullable=True)
    ports = Column(NestedMutableJson, nullable=False)
    gpp = Column(NestedMutableJson, nullable=True)
    error = Column(String, nullable=True)


class DBJobInfo(Base):
    __tablename__ = 'job_info'
    id = Column(String(64), primary_key=True)
    batch_id = Column(String(64), nullable=True)
    proc_id = Column(String(64), nullable=False)
    user_iid = Column(String(64), nullable=False)
    status = Column(NestedMutableJson, nullable=False)
    job = Column(NestedMutableJson, nullable=False)
    runner = Column(NestedMutableJson, nullable=False)


class RTIServiceBase(RTIRESTService):
    def __init__(self, node, db_path: str, retain_job_history: bool, strict_deployment: bool):
        super().__init__(retain_job_history, strict_deployment)

        # initialise properties
        self._mutex = threading.Lock()
        self._node = node

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

    @abc.abstractmethod
    def perform_deploy(self, proc: Processor) -> None:
        ...

    @abc.abstractmethod
    def perform_undeploy(self, proc: Processor, keep_image: bool = True) -> None:
        ...

    @abc.abstractmethod
    def perform_submit_single(self, job: Job, proc: Processor) -> None:
        ...

    @abc.abstractmethod
    def perform_submit_batch(self, batch: List[Tuple[Job, JobStatus, Processor]], batch_id: str) -> None:
        ...

    @abc.abstractmethod
    def perform_cancel(self, job_id: str, peer_address: P2PAddress, grace_period: int = 30) -> None:
        ...

    @abc.abstractmethod
    def perform_purge(self, job_record: DBJobInfo) -> None:
        ...

    @abc.abstractmethod
    def resolve_port_mapping(self, job_id: str, runner_details: dict) -> dict:
        ...

    def update_job(self, job_id: str, runner_identity: Identity, runner_address: str) -> Job:
        with self._mutex:
            with self._session_maker() as session:
                # get the DB record for the job (if any)
                record = session.query(DBJobInfo).get(job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist")

                # update the runner information
                record.runner['identity'] = runner_identity.model_dump()
                record.runner['address'] = runner_address

                # resolve the port mapping
                resolved_ports = self._node.rti.resolve_port_mapping(job_id, dict(record.runner))
                record.runner['ports'] = resolved_ports

                session.commit()

                # make the runner identity known to the node
                self._node.db.update_identity(runner_identity)

                return Job.model_validate(record.job)

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
                                        image_name=record.image_name, ports=list(record.ports),
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
                                 image_name=record.image_name, ports=list(record.ports),
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
            proc = Processor(
                id=proc_id, state=Processor.State.BUSY_DEPLOY, image_name=None, ports=None, gpp=None, error=None
            )

            # update or create db record
            with self._session_maker() as session:
                record = session.query(DBDeployedProcessor).get(proc.id)
                if record:
                    record.state = proc.state.value
                    record.image_name = proc.image_name
                    record.ports = proc.ports
                    record.gpp = proc.gpp.model_dump()
                    record.error = proc.error

                else:
                    session.add(DBDeployedProcessor(id=proc.id, state=proc.state.value, image_name=proc.image_name,
                                                    ports=[], gpp=proc.gpp.model_dump() if proc.gpp else None,
                                                    error=None))
                session.commit()

            # start the deployment worker
            threading.Thread(target=self.perform_deploy, args=(proc,)).start()

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
                                 image_name=record.image_name, ports=list(record.ports),
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
                    threading.Thread(target=self.perform_undeploy, args=(proc, )).start()

                # is the state busy going up? -> throw error
                elif proc.state == Processor.State.BUSY_DEPLOY:
                    raise RTIException("Cannot undeploy a processor that is currently deploying. Try again later.")

                # is the state busy going down? -> do nothing
                elif proc.state == Processor.State.BUSY_DEPLOY:
                    logger.warning(f"[undeploy:{shorten_id(proc_id)}] already undeploying.")

                return proc

    def rest_submit(self, tasks: List[Task], request: Request) -> List[Job]:
        """
        Submits one or more tasks to be processed. If multiple tasks are submitted, they will be executed in a
        coupled manner, i.e., their start-up will be synchronised and they are made aware of each other in order
        to facilitate co-execution.
        """

        # get the user's identity and check if it's identical with that's indicated in the task
        iid = request.headers['saasauth-iid']
        for task in tasks:
            if iid != task.user_iid:
                raise RTIException("Mismatch between user indicated in task and user making request", details={
                    'iid': iid,
                    'task': task
                })

        return self.submit(tasks)

    def submit(self, tasks: List[Task]) -> List[Job]:
        """
        Submits one or more tasks to be processed. If multiple tasks are submitted, they will be executed in a
        coupled manner, i.e., their start-up will be synchronised and they are made aware of each other in order
        to facilitate co-execution.
        """

        # perform a few checks
        user_iids: List[str] = list(set([task.user_iid for task in tasks]))
        if len(tasks) > 1:
            # if we run these tasks in-sync then it has to be the same user.
            if len(user_iids) != 1:
                raise RTIException(f"Multiple users for job submission: {', '.join(user_iids)}")

            # job names are mandatory for easier distinction between jobs in a batch
            missing_names: List[Task] = [task for task in tasks if task.name is None]
            if missing_names:
                raise RTIException(f"Job name missing in {len(missing_names)} of {len(tasks)} tasks "
                                   f"(job names are mandatory for batch submission")

        # get the identity of the user
        user: Identity = self._node.db.get_identity(user_iids[0])

        # if this is a batch, create a batch id
        batch_id: Optional[str] = generate_random_string(8) if len(tasks) > 1 else None

        # for each task prepare the job
        batch: List[Tuple[Job, JobStatus, Processor]] = []
        for task in tasks:
            # get the processor
            proc: Optional[Processor] = self.get_proc(task.proc_id)
            if proc is None:
                raise ProcessorNotDeployedError({
                    'proc_id': task.proc_id
                })

            # create the job folder with a generated job id
            job_id = generate_random_string(8)
            job_path = os.path.join(self._jobs_path, job_id)
            os.makedirs(job_path, exist_ok=True)

            # create the initial job descriptor and write to file
            job = Job(id=job_id, batch_id=batch_id, task=task, retain=self._retain_job_history,
                      custodian=self._node.info, proc_name=proc.gpp.proc_descriptor.name,
                      t_submitted=get_timestamp_now())
            descriptor_path = os.path.join(job_path, 'job.descriptor')
            with open(descriptor_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(job.model_dump(), f, indent=2)

            # create initial job status and write to file
            status = JobStatus(state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={},
                               errors=[], message=None)
            status_path = os.path.join(job_path, 'job.status')
            with open(status_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(status.model_dump(), f, indent=2)

            # create initial job info record and write to database
            with self._session_maker() as session:
                record = DBJobInfo(
                    id=job.id,
                    batch_id=batch_id,
                    proc_id=task.proc_id,
                    user_iid=user.id,
                    status=status.model_dump(),
                    job=job.model_dump(),
                    runner={
                        'ports': {f"{port}/{protocol}": None for port, protocol in proc.ports}
                    })
                session.add(record)
                session.commit()

            # Add the job to the batch
            batch.append((job, status, proc))

        try:
            if len(batch) == 1:
                # perform submission of the single task
                job, _, proc = batch[0]
                self.perform_submit_single(job, proc)

            else:
                # perform submission of the batch of tasks
                self.perform_submit_batch(batch, batch_id)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"[submit] failed: {trace}")

            # update the runner information of all involved jobs
            with self._session_maker() as session:
                for job, status, _ in batch:
                    status.state = JobStatus.State.FAILED
                    status.errors.append(JobStatus.Error(
                        message=str(e),
                        exception=ExceptionContent(
                            id='', reason=str(e), details={'trace': trace}
                        )
                    ))
                    record = session.query(DBJobInfo).get(job.id)
                    record.status = status
                session.commit()

        return [item[0] for item in batch]

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

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        """
        Retrieves detailed information about the status of a batch of jobs. Authorisation is required by the owner of
        the batch (i.e., the user that has created the batch by submitting the tasks in the first place).
        """
        members: List[BatchStatus.Member] = []
        with self._mutex:
            with self._session_maker() as session:
                # get the records
                records = session.query(DBJobInfo).filter_by(batch_id=batch_id).all()
                if records is None:
                    raise RTIException(f"Batch {batch_id} does not exist.")

                # determine the batch user iid (all jobs have the same user iid)
                user_iid = records[0].user_iid

                # create member items
                for record in records:
                    job = Job.model_validate(record.job)
                    status = JobStatus.model_validate(record.status)
                    identity = Identity.model_validate(
                        record.runner['identity']
                    ) if 'identity' in record.runner else None
                    members.append(BatchStatus.Member(
                        name=job.task.name,
                        job_id=job.id,
                        state=status.state,
                        identity=identity,
                        ports=record.runner['ports']
                    ))

        return BatchStatus(
            batch_id=batch_id,
            user_iid=user_iid,
            members=members
        )

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

        # if possible, determine the runner P2P address
        if record.runner.get('identity') is not None and record.runner.get('address') is not None:
            runner = Identity.model_validate(record.runner['identity'])
            runner_address = P2PAddress(
                address=record.runner['address'],
                curve_secret_key=self._node.keystore.curve_secret_key(),
                curve_public_key=self._node.keystore.curve_public_key(),
                curve_server_key=runner.c_public_key
            )
        else:
            runner_address = None

        # start the cancellation worker
        threading.Thread(target=self.perform_cancel, args=(job_id, runner_address)).start()

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

                # perform the purge
                self.perform_purge(record)

                # delete the record
                session.delete(record)
                session.commit()

                status = JobStatus.model_validate(record.status)
                return status
