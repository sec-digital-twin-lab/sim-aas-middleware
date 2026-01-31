import abc
import asyncio
import json
import os
import threading
import traceback
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Set

from fastapi.requests import Request

from simaas.nodedb.schemas import NamespaceInfo, NodeInfo, ResourceDescriptor
from simaas.core.exceptions import ExceptionContent
from simaas.core.helpers import generate_random_string, get_timestamp_now
from simaas.cli.helpers import shorten_id
from simaas.dor.schemas import GitProcessorPointer
from simaas.core.identity import Identity
from simaas.rti.exceptions import RTIException
from simaas.rti.schemas import JobStatus, Processor, Task, Job, BatchStatus, ProcessorVolume
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
    volumes = Column(NestedMutableJson, nullable=False)
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


@dataclass
class TaskChecklist:
    task: Task
    proc: Optional[Processor]
    job_id: Optional[str]
    peers: List[NodeInfo]
    job: Optional[Job]
    status: Optional[JobStatus]


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

        # a map of active cancellation workers (async tasks)
        self._cancellation_workers: Dict[str, Optional[asyncio.Task]] = {}
        self._cleanup_workers: Dict[str, Optional[asyncio.Task]] = {}
        self._deploy_workers: Dict[str, Optional[asyncio.Task]] = {}
        self._undeploy_workers: Dict[str, Optional[asyncio.Task]] = {}

    def on_cancellation_worker_done(self, job_id: str) -> None:
        # we keep the dict entry but remove the thread object
        self._cancellation_workers[job_id] = None

    def on_cleanup_worker_done(self, job_id: str) -> None:
        # we keep the dict entry but remove the thread object
        self._cleanup_workers[job_id] = None

    def on_deploy_worker_done(self, proc_id: str) -> None:
        # we keep the dict entry but remove the task object
        self._deploy_workers[proc_id] = None

    def on_undeploy_worker_done(self, proc_id: str) -> None:
        # we keep the dict entry but remove the task object
        self._undeploy_workers[proc_id] = None

    def mark_job_cancelled(self, job_id: str) -> None:
        """Mark a job as cancelled in the database. Called by perform_cancel implementations
        when the container/instance has stopped but the job may not have pushed its status.

        This is a simple DB update without triggering cascade logic, since the cascade
        was already triggered by the original failure that caused the cancellation."""
        with self._session_maker() as session:
            record = session.get(DBJobInfo, job_id)
            if record is None:
                return  # job doesn't exist, nothing to do

            status = JobStatus.model_validate(record.status)
            # only update if not already in a terminal state
            if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                return  # already in terminal state, nothing to do

            # Update to CANCELLED directly in DB
            status.state = JobStatus.State.CANCELLED
            record.status = status.model_dump()
            session.commit()

            # Also update the local status file
            try:
                status_path = os.path.join(self._jobs_path, job_id, 'job.status')
                with open(status_path, 'w') as f:
                    json.dump(status.model_dump(), f, indent=2)
            except Exception:
                pass  # Non-critical, just for local file consistency

    def has_active_workers(self) -> bool:
        all_workers = (
            list(self._cancellation_workers.values()) +
            list(self._cleanup_workers.values()) +
            list(self._deploy_workers.values()) +
            list(self._undeploy_workers.values())
        )
        return any(
            worker is not None for worker in all_workers
        )

    def update_proc_db(self, proc: Processor) -> None:
        # update or create db record
        with self._session_maker() as session:
            record = session.get(DBDeployedProcessor, proc.id)
            if record:
                record.state = proc.state.value
                record.image_name = proc.image_name
                record.ports = proc.ports
                record.gpp = proc.gpp.model_dump()
                record.error = proc.error
                record.volumes = [volume.model_dump() for volume in proc.volumes]

            else:
                session.add(DBDeployedProcessor(id=proc.id, state=proc.state.value, image_name=proc.image_name,
                                                ports=[], volumes=[volume.model_dump() for volume in proc.volumes],
                                                gpp=proc.gpp.model_dump() if proc.gpp else None,
                                                error=proc.error))
            session.commit()

    @abc.abstractmethod
    async def perform_deploy(self, proc: Processor) -> None:
        ...

    @abc.abstractmethod
    async def perform_undeploy(self, proc: Processor, keep_image: bool = True) -> None:
        ...

    @abc.abstractmethod
    def perform_submit_single(self, job: Job, proc: Processor) -> None:
        ...

    @abc.abstractmethod
    def perform_submit_batch(self, batch: List[Tuple[Job, JobStatus, Processor]], batch_id: str) -> None:
        ...

    @abc.abstractmethod
    async def perform_cancel(self, job_id: str, peer_address: P2PAddress, grace_period: int = 30) -> None:
        ...

    @abc.abstractmethod
    async def perform_purge(self, job_record: DBJobInfo) -> None:
        ...

    @abc.abstractmethod
    async def perform_job_cleanup(self, job_id: str) -> None:
        ...

    @abc.abstractmethod
    def resolve_port_mapping(self, job_id: str, runner_details: dict) -> dict:
        ...

    async def update_job(self, job_id: str, runner_identity: Identity, runner_address: str) -> Job:
        # DB operations - no lock needed, session is per-call
        with self._session_maker() as session:
            # get the DB record for the job (if any)
            record = session.get(DBJobInfo, job_id)
            if record is None:
                raise RTIException(f"Job {job_id} does not exist")

            # update the runner information
            record.runner['identity'] = runner_identity.model_dump()
            record.runner['address'] = runner_address

            # resolve the port mapping
            resolved_ports = self._node.rti.resolve_port_mapping(job_id, dict(record.runner))
            record.runner['ports'] = resolved_ports

            session.commit()

            # extract job before session closes
            job = Job.model_validate(record.job)

        # async operation outside session/lock
        await self._node.db.update_identity(runner_identity)

        return job

    def is_deployed(self, proc_id: str) -> bool:
        with self._session_maker() as session:
            record = session.get(DBDeployedProcessor, proc_id)
            return record is not None

    async def get_all_procs(self) -> List[Processor]:
        """
        Retrieves a list of all deployed processors
        """
        with self._session_maker() as session:
            records = session.query(DBDeployedProcessor).all()
            result = []
            for record in records:
                result.append(Processor(id=record.id, state=Processor.State(record.state),
                                        image_name=record.image_name, ports=list(record.ports),
                                        volumes=[ProcessorVolume.model_validate(v) for v in record.volumes],
                                        gpp=GitProcessorPointer.model_validate(record.gpp) if record.gpp else None,
                                        error=record.error))

            return result

    async def get_proc(self, proc_id: str) -> Optional[Processor]:
        """
        Retrieves a specific processors given its id.
        """
        with self._session_maker() as session:
            record = session.get(DBDeployedProcessor, proc_id)
            if record:
                return Processor(id=record.id, state=Processor.State(record.state),
                                 image_name=record.image_name, ports=list(record.ports),
                                 volumes=[ProcessorVolume.model_validate(v) for v in record.volumes],
                                 gpp=GitProcessorPointer.model_validate(record.gpp) if record.gpp else None,
                                 error=record.error)
            else:
                return None

    async def deploy(self, proc_id: str, volumes: Optional[List[ProcessorVolume]] = None) -> Processor:
        """
        Deploys a processor.
        """

        # is the processor already deployed?
        proc = await self.get_proc(proc_id)
        if proc is not None:
            return proc

        # create a placeholder processor object
        proc = Processor(
            id=proc_id, state=Processor.State.BUSY_DEPLOY, image_name=None, ports=None,
            volumes=volumes if volumes else [], gpp=None, error=None
        )

        # update or create db record - no lock needed, DB handles conflicts
        await asyncio.to_thread(self.update_proc_db, proc)

        # start the deployment worker as async task with tracking
        task = asyncio.create_task(self.perform_deploy(proc))
        task.add_done_callback(lambda _: self.on_deploy_worker_done(proc.id))
        with self._mutex:
            self._deploy_workers[proc.id] = task

        return proc

    async def undeploy(self, proc_id: str) -> Optional[Processor]:
        """
        Removes a processor from the RTI (if it exists).
        """
        start_undeploy = False

        # DB operations - no lock needed, session is per-call
        with self._session_maker() as session:
            # do we have a db record for this processor?
            record = session.get(DBDeployedProcessor, proc_id)
            if not record:
                return None

            # create the processor object
            proc = Processor(id=record.id, state=Processor.State(record.state),
                             image_name=record.image_name, ports=list(record.ports),
                             volumes=[ProcessorVolume.model_validate(v) for v in record.volumes],
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
                start_undeploy = True

            # is the state busy going up? -> throw error
            elif proc.state == Processor.State.BUSY_DEPLOY:
                raise RTIException("Cannot undeploy a processor that is currently deploying. Try again later.")

            # is the state busy going down? -> do nothing
            elif proc.state == Processor.State.BUSY_UNDEPLOY:
                logger.warning(f"[undeploy:{shorten_id(proc_id)}] already undeploying.")

        # start the worker as async task with tracking - outside any lock
        if start_undeploy:
            task = asyncio.create_task(self.perform_undeploy(proc))
            task.add_done_callback(lambda _: self.on_undeploy_worker_done(proc.id))
            with self._mutex:
                self._undeploy_workers[proc.id] = task

        return proc

    async def rest_submit(self, tasks: List[Task], request: Request) -> List[Job]:
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

        return await self.submit(tasks)

    async def cancel_resource_reservations(self, checklists: List[TaskChecklist]) -> None:
        for checklist in checklists:
            # there MIGHT BE a reservation if we have a namespace
            if checklist.task.namespace is None:
                continue

            # send the cancel message to all nodes in the network
            await self._node.db.cancel_namespace_reservation(checklist.task.namespace, checklist.job_id)

    async def check_submitted_tasks(self, tasks: List[Task]) -> List[TaskChecklist]:
        # create the checklists for each task
        checklists: List[TaskChecklist] = []
        for task in tasks:
            checklists.append(TaskChecklist(
                task=task,
                proc=await self.get_proc(task.proc_id),
                job_id=generate_random_string(8),
                peers=[], job=None, status=None
            ))

        unique_user_iids: Set[str] = set()
        unique_task_names: Set[str] = set()
        for checklist in checklists:
            unique_user_iids.add(checklist.task.user_iid)

            # task names are mandatory for easier distinction in a batch
            if len(checklists) > 1 and checklist.task.name is None:
                raise RTIException("Missing name for task which is member of batch")

            # check if the task name is unique
            if checklist.task.name in unique_task_names:
                raise RTIException(f"Duplicate task name '{checklist.task.name}' (task names must be unique)")
            else:
                unique_task_names.add(checklist.task.name)

            # check if the required processor is deployed for each task
            if checklist.proc is None:
                raise RTIException(f"Processor {checklist.task.proc_id} required by task but not deployed")

        # there should be only one user iid
        if len(unique_user_iids) > 1:
            raise RTIException(f"Multiple users for batch job submission: {', '.join(unique_user_iids)}")

        # check if the node knows about the user identities
        user: Optional[Identity] = await self._node.db.get_identity(tasks[0].user_iid)
        if user is None:
            raise RTIException(f"User identity {tasks[0].user_iid} unknown")

        # check if any of the tasks require resource reservations
        combined: Dict[str, Tuple[ResourceDescriptor, NamespaceInfo]] = {}
        for checklist in checklists:
            task = checklist.task

            # does this task require resource reservation (it does if it is using a namespace)
            if task.namespace is None:
                continue

            # if namespace is given a budget must be given as well, if not -> error
            if task.budget is None:
                raise RTIException(f"Task {task.name} missing resource budget")

            # make sure we have a tuple in combined for this namespace
            if task.namespace not in combined:
                combined[task.namespace] = (
                    ResourceDescriptor(vcpus=0, memory=0), await self._node.db.get_namespace(task.namespace)
                )

            ns_budget, ns_info = combined[task.namespace]

            # can the budget of the task be satisfied by the namespace in principle?
            if task.budget.vcpus > ns_info.budget.vcpus or task.budget.memory > ns_info.budget.memory:
                raise RTIException(f"Task {task.name} exceeds namespace resource capacity")

            # add to the combined resource budget
            ns_budget.vcpus += task.budget.vcpus
            ns_budget.memory += task.budget.memory

        # check if the combined required resource budget exceeds the namespace capacity
        for namespace in combined.keys():
            ns_budget, ns_info = combined[namespace]
            if ns_budget.vcpus > ns_info.budget.vcpus or ns_budget.memory > ns_info.budget.memory:
                raise RTIException(
                    f"Combined resource budget for namespace '{namespace}' exceeds namespace capacity: "
                    f"{ns_budget.vcpus} vCPUs + {ns_budget.memory} MB mem > "
                    f"{ns_info.budget.vcpus} vCPUs + {ns_info.budget.memory} MB mem"
                )

        return checklists

    async def prepare_job_execution(self, batch_id: Optional[str], checklists: List[TaskChecklist]) -> None:
        # try to make reservations for all tasks that require it
        successful = True
        try:
            for checklist in checklists:
                # does the task require a resource reservation?
                if checklist.task.namespace is not None:
                    await self._node.db.reserve_namespace_resources(
                        checklist.task.namespace, checklist.job_id, checklist.task.budget
                    )

        except Exception:
            successful = False

        # if there was a problem at any point of the reservation process, cancel all reservations (if any)
        if not successful:
            await self.cancel_resource_reservations(checklists)
            raise RTIException("Failed to reserve resources for tasks")

        # try to prepare the job for each task
        for checklist in checklists:
            # create the job folder with a generated job id
            job_path = os.path.join(self._jobs_path, checklist.job_id)
            os.makedirs(job_path, exist_ok=True)

            # create the initial job descriptor and write to file
            checklist.job = Job(id=checklist.job_id, batch_id=batch_id, task=checklist.task,
                                retain=self._retain_job_history, custodian=self._node.info,
                                proc_name=checklist.proc.gpp.proc_descriptor.name,
                                t_submitted=get_timestamp_now())
            descriptor_path = os.path.join(job_path, 'job.descriptor')
            with open(descriptor_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(checklist.job.model_dump(), f, indent=2)

            # create initial job status and write to file
            checklist.status = JobStatus(state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={},
                                         errors=[], message=None)
            status_path = os.path.join(job_path, 'job.status')
            with open(status_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(checklist.status.model_dump(), f, indent=2)

    async def perform_batch_submission(self, batch_id: Optional[str], checklists: List[TaskChecklist]) -> None:
        with self._session_maker() as session:
            # assemble the batch and create initial job DB records
            batch: List[Tuple[Job, JobStatus, Processor]] = []
            for checklist in checklists:
                # create the initial job record
                session.add(DBJobInfo(
                    id=checklist.job.id,
                    batch_id=batch_id,
                    proc_id=checklist.task.proc_id,
                    user_iid=checklist.task.user_iid,
                    status=checklist.status.model_dump(),
                    job=checklist.job.model_dump(),
                    runner={
                        'ports': {f"{port}/{protocol}": None for port, protocol in checklist.proc.ports}
                    }
                ))

                # add the job to the batch
                batch.append((checklist.job, checklist.status, checklist.proc))

            session.commit()

        # perform job submission of the entire batch of jobs
        error, trace = None, None
        try:
            if len(checklists) == 1:
                # perform submission of the single task
                self.perform_submit_single(batch[0][0], batch[0][2])

            else:
                # perform submission of the batch of tasks
                self.perform_submit_batch(batch, batch_id)

        except Exception as e:
            error = str(e)
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"[submit] error while performing batch submission: {trace}")

        # if there was any error during batch submission, we need to clean-up whatever might be there/left
        if error and trace:
            with self._session_maker() as session:
                for job, status, _ in batch:
                    # purge job that may already be running
                    record: Optional[DBJobInfo] = session.get(DBJobInfo, job.id)
                    if record is not None:
                        try:
                            await self.perform_purge(record)
                        except Exception as e:
                            logger.warning(f"[submit] purge {job.id} failed as part of batch termination: {e}")

                    # update the runner information
                    status.state = JobStatus.State.FAILED
                    status.errors.append(JobStatus.Error(
                        message=error,
                        exception=ExceptionContent(
                            id='', reason=f"Submission of batch {batch_id} failed", details={'trace': trace}
                        )
                    ))
                    record = session.get(DBJobInfo, job.id)
                    record.status = status

                session.commit()

            # cancel resource reservations (if any left for whatever reason)
            await self.cancel_resource_reservations(checklists)

            raise RTIException(f"Error while submitting jobs for Batch {batch_id}")

    async def submit(self, tasks: List[Task]) -> List[Job]:
        """
        Submits one or more tasks to be processed. If multiple tasks are submitted, they will be executed in a
        coupled manner, i.e., their start-up will be synchronised and they are made aware of each other in order
        to facilitate co-execution.
        """

        # perform a series of checks
        checklists: List[TaskChecklist] = await self.check_submitted_tasks(tasks)

        # if this is a batch, create a batch id
        batch_id: Optional[str] = generate_random_string(8) if len(tasks) > 1 else None

        # prepare job execution
        await self.prepare_job_execution(batch_id, checklists)

        # submit the prepared batch
        await self.perform_batch_submission(batch_id, checklists)

        return [checklist.job for checklist in checklists]

    async def jobs_by_proc(self, proc_id: str) -> List[Job]:
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

    async def jobs_by_user(self, request: Request) -> List[Job]:
        """
        Retrieves a list of active jobs by a user. If the user is the node owner, all active jobs will be returned.
        """
        # get the records
        user: Identity = await self._node.db.get_identity(request.headers['saasauth-iid'])
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

    async def update_job_status(self, job_id: str, job_status: JobStatus) -> None:
        """
        Updates the status of a particular job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """
        # Collect async work to do (populated during DB operations)
        namespace_to_cancel: Optional[Tuple[str, str]] = None  # (namespace, job_id)
        job_to_cleanup: Optional[str] = None
        jobs_to_cancel: List[Tuple[str, Optional[P2PAddress]]] = []  # (job_id, runner_address)

        # DB operations - no lock needed, session is per-call
        with self._session_maker() as session:
            # get the record
            record: DBJobInfo = session.get(DBJobInfo, job_id)
            if record is None:
                raise RTIException(f"Job {job_id} does not exist.")

            # check current state - don't allow overwriting terminal states
            current_status = JobStatus.model_validate(record.status)
            if current_status.state in [JobStatus.State.CANCELLED, JobStatus.State.FAILED, JobStatus.State.SUCCESSFUL]:
                if job_status.state != current_status.state:
                    # trying to change a terminal state - ignore
                    logger.warning(f"Job {job_id} already in terminal state {current_status.state}, "
                                   f"ignoring update to {job_status.state}")
                    return

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

            # do we need to cancel a namespace reservation?
            if job_status.state in [
                JobStatus.State.FAILED, JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED
            ]:
                # cancel resource reservation (if applicable)
                job: Job = Job.model_validate(record.job)
                if job.task.namespace is not None:
                    namespace_to_cancel = (job.task.namespace, job.id)

                # check if cleanup needed (short lock for dict access)
                with self._mutex:
                    if job.id not in self._cleanup_workers:
                        job_to_cleanup = job.id
                        self._cleanup_workers[job.id] = None  # placeholder

            # is this job part of a batch?
            batch_records: Optional[List[DBJobInfo]] = \
                session.query(DBJobInfo).filter_by(
                    batch_id=record.batch_id).all() if record.batch_id else None

            # do we need to terminate related jobs?
            if batch_records is not None and job_status.state in [
                JobStatus.State.FAILED, JobStatus.State.CANCELLED
            ]:
                for related in batch_records:
                    # skip if this is the record of the just updated job
                    if related.id == job_id:
                        continue

                    # check the status and collect jobs to cancel
                    related_status = JobStatus.model_validate(related.status)
                    if related_status.state in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED,
                                                JobStatus.State.RUNNING]:
                        # short lock for dict check-then-add
                        with self._mutex:
                            if related.id in self._cancellation_workers:
                                continue
                            self._cancellation_workers[related.id] = None  # placeholder

                        logger.info(f"Job {job_id} failed/cancelled -> "
                                    f"related job {related.id} status {related_status.state} -> cancel")

                        # extract runner info while session is open
                        if related.runner.get('identity') is not None and related.runner.get('address') is not None:
                            runner = Identity.model_validate(related.runner['identity'])
                            runner_address = P2PAddress(
                                address=related.runner['address'],
                                curve_secret_key=self._node.keystore.curve_secret_key(),
                                curve_public_key=self._node.keystore.curve_public_key(),
                                curve_server_key=runner.c_public_key
                            )
                        else:
                            runner_address = None

                        jobs_to_cancel.append((related.id, runner_address))
                    else:
                        logger.info(f"Job {job_id} failed/cancelled -> "
                                    f"related job {related.id} status {related_status.state} -> skip")

        # Async operations - outside any lock
        if namespace_to_cancel:
            await self._node.db.cancel_namespace_reservation(namespace_to_cancel[0], namespace_to_cancel[1])

        if job_to_cleanup:
            task = asyncio.create_task(self.perform_job_cleanup(job_to_cleanup))
            with self._mutex:
                self._cleanup_workers[job_to_cleanup] = task

        for cancel_job_id, runner_address in jobs_to_cancel:
            task = asyncio.create_task(self.perform_cancel(cancel_job_id, runner_address))
            with self._mutex:
                self._cancellation_workers[cancel_job_id] = task

    async def get_job_owner_iid(self, job_id: str) -> str:
        with self._mutex:
            with self._session_maker() as session:
                record = session.get(DBJobInfo, job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")
                return record.user_iid

    async def get_job_status(self, job_id: str) -> JobStatus:
        """
        Retrieves detailed information about the status of a job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """
        # get the record
        with self._mutex:
            with self._session_maker() as session:
                record = session.get(DBJobInfo, job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

        return JobStatus.model_validate(record.status)

    async def get_batch_status(self, batch_id: str) -> BatchStatus:
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

    def _job_cancel_internal(self, record: DBJobInfo) -> JobStatus:
        # check the status
        status = JobStatus.model_validate(record.status)
        if status.state not in [JobStatus.State.UNINITIALISED, JobStatus.State.INITIALISED, JobStatus.State.RUNNING]:
            raise RTIException(f"Job {record.id} is not active -> job cannot be cancelled.")

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

        # start the cancellation worker as async task
        task = asyncio.create_task(self.perform_cancel(record.id, runner_address))
        with self._mutex:
            self._cancellation_workers[record.id] = task

        return status

    async def job_cancel(self, job_id: str) -> JobStatus:
        """
        Attempts to cancel a running job. Depending on the implementation of the processor, this may or may not be
        possible.
        """
        # get the record
        with self._mutex:
            with self._session_maker() as session:
                record = session.get(DBJobInfo, job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

        return self._job_cancel_internal(record)

    async def job_purge(self, job_id: str) -> JobStatus:
        """
        Purges a running job. It will be removed regardless of its state.
        """
        # remove the job from database
        with self._mutex:
            with self._session_maker() as session:
                # get the record
                record: Optional[DBJobInfo] = session.get(DBJobInfo, job_id)
                if record is None:
                    raise RTIException(f"Job {job_id} does not exist.")

                # perform the purge
                await self.perform_purge(record)

                # delete the record
                session.delete(record)
                session.commit()

                status = JobStatus.model_validate(record.status)
                return status
