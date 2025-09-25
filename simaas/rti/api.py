from __future__ import annotations

import abc

from typing import List, Tuple, Optional
from fastapi import Request

from simaas.decorators import requires_proc_deployed, requires_authentication, requires_job_or_node_ownership, \
    requires_proc_not_busy, requires_node_ownership_if_strict, requires_tasks_supported, \
    requires_batch_or_node_ownership
from simaas.rest.schemas import EndpointDefinition
from simaas.rest.proxy import EndpointProxy, Session, get_proxy_prefix

from simaas.core.keystore import Keystore
from simaas.rti.schemas import Job, JobStatus, Processor, Task, BatchStatus, ProcessorVolume

RTI_ENDPOINT_PREFIX = "/api/v1/rti"
JOB_ENDPOINT_PREFIX = "/api/v1/job"


class RTIAdminInterface(abc.ABC):
    @abc.abstractmethod
    @requires_node_ownership_if_strict
    def deploy(self, proc_id: str, volumes: Optional[List[ProcessorVolume]] = None) -> Processor:
        """
        Deploys a processor.
        """

    @abc.abstractmethod
    @requires_node_ownership_if_strict
    @requires_proc_deployed
    @requires_proc_not_busy
    def undeploy(self, proc_id: str) -> Optional[Processor]:
        """
        Removes a processor from the RTI (if it exists).
        """

    @abc.abstractmethod
    @requires_proc_deployed
    def jobs_by_proc(self, proc_id: str) -> List[Job]:
        """
        Retrieves a list of active jobs processed by a processor. Any job that is pending execution or actively
        executed will be included in the list.
        """

    @abc.abstractmethod
    @requires_job_or_node_ownership
    def update_job_status(self, job_id: str, job_status: JobStatus) -> None:
        """
        Updates the status of a particular job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """

    @abc.abstractmethod
    def get_job_owner_iid(self, job_id: str) -> str:
        ...


class RTIInterface(abc.ABC):
    @abc.abstractmethod
    def type(self) -> str:
        ...

    @abc.abstractmethod
    def get_all_procs(self) -> List[Processor]:
        """
        Retrieves a dict of all deployed processors by their id.
        """

    @abc.abstractmethod
    def get_proc(self, proc_id: str) -> Optional[Processor]:
        """
        Retrieves a specific processors given its id.
        """

    @abc.abstractmethod
    @requires_tasks_supported
    def submit(self, tasks: List[Task]) -> List[Job]:
        """
        Submits one or more tasks to be processed. If multiple tasks are submitted, they will be executed in a
        coupled manner, i.e., their start-up will be synchronised and they are made aware of each other in order
        to facilitate co-execution.
        """

    @abc.abstractmethod
    @requires_job_or_node_ownership
    def get_job_status(self, job_id: str) -> JobStatus:
        """
        Retrieves detailed information about the status of a job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """

    @abc.abstractmethod
    @requires_batch_or_node_ownership
    def get_batch_status(self, batch_id: str) -> BatchStatus:
        """
        Retrieves detailed information about the status of a batch of jobs. Authorisation is required by the owner of
        the batch (i.e., the user that has created the batch by submitting the tasks in the first place).
        """

    @abc.abstractmethod
    @requires_job_or_node_ownership
    def job_cancel(self, job_id: str) -> JobStatus:
        """
        Attempts to cancel a running job. Depending on the implementation of the processor, this may or may not be
        possible.
        """

    @abc.abstractmethod
    @requires_job_or_node_ownership
    def job_purge(self, job_id: str) -> JobStatus:
        """
        Purges a running job. It will be removed regardless of its state.
        """


class RTIRESTService(RTIAdminInterface, RTIInterface, abc.ABC):
    def __init__(self, retain_job_history: bool, strict_deployment: bool):
        self._retain_job_history = retain_job_history
        self._strict_deployment = strict_deployment

    @property
    def retain_job_history(self) -> bool:
        return self._retain_job_history

    @property
    def strict_deployment(self) -> bool:
        return self._strict_deployment

    @abc.abstractmethod
    @requires_tasks_supported
    @requires_authentication
    def rest_submit(self, tasks: List[Task], request: Request) -> List[Job]:
        """
        Submits one or more tasks to be processed. If multiple tasks are submitted, they will be executed in a
        coupled manner, i.e., their start-up will be synchronised and they are made aware of each other in order
        to facilitate co-execution.
        """

    @abc.abstractmethod
    @requires_authentication
    def jobs_by_user(self, request: Request) -> List[Job]:
        """
        Retrieves a list of active jobs by a user. If the user is the node owner, all active jobs will be returned.
        """

    def endpoints(self) -> List[EndpointDefinition]:
        return [
            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'proc', self.get_all_procs, List[Processor]),
            EndpointDefinition('POST', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}', self.deploy, Processor),
            EndpointDefinition('DELETE', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}', self.undeploy, Processor),
            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}', self.get_proc, Optional[Processor]),
            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}/jobs', self.jobs_by_proc, List[Job]),

            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'job', self.jobs_by_user, List[Job]),
            EndpointDefinition('POST', RTI_ENDPOINT_PREFIX, 'job', self.rest_submit, List[Job]),
            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'job/{job_id}/status', self.get_job_status, JobStatus),
            EndpointDefinition('DELETE', RTI_ENDPOINT_PREFIX, 'job/{job_id}/cancel', self.job_cancel, JobStatus),
            EndpointDefinition('DELETE', RTI_ENDPOINT_PREFIX, 'job/{job_id}/purge', self.job_purge, JobStatus),

            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'batch/{batch_id}/status', self.get_batch_status, BatchStatus),
        ]


class RTIProxy(EndpointProxy):
    @classmethod
    def from_session(cls, session: Session) -> RTIProxy:
        return RTIProxy(remote_address=session.address, credentials=session.credentials,
                        endpoint_prefix=(session.endpoint_prefix_base, 'rti'))

    def __init__(self, remote_address: (str, int), credentials: (str, str) = None,
                 endpoint_prefix: Tuple[str, str] = get_proxy_prefix(RTI_ENDPOINT_PREFIX)):
        super().__init__(endpoint_prefix, remote_address, credentials=credentials)

    def get_all_procs(self) -> List[Processor]:
        results = self.get("proc")
        return [Processor.model_validate(result) for result in results]

    def get_proc(self, proc_id: str) -> Optional[Processor]:
        result = self.get(f"proc/{proc_id}")
        return Processor.model_validate(result) if result else None

    def deploy(self, proc_id: str, authority: Keystore, volumes: Optional[List[ProcessorVolume]] = None) -> Processor:
        body = [volume.model_dump() for volume in volumes] if volumes else None
        result = self.post(f"proc/{proc_id}", body=body, with_authorisation_by=authority)
        return Processor.model_validate(result)

    def undeploy(self, proc_id: str, authority: Keystore) -> Processor:
        result = self.delete(f"proc/{proc_id}", with_authorisation_by=authority)
        return Processor.model_validate(result)

    def submit(self, tasks: List[Task], with_authorisation_by: Keystore) -> List[Job]:
        body = [task.model_dump() for task in tasks]
        results = self.post("job", body=body, with_authorisation_by=with_authorisation_by)
        return [Job.model_validate(result) for result in results]

    def get_jobs_by_proc(self, proc_id: str) -> List[Job]:
        results = self.get(f"proc/{proc_id}/jobs")
        return [Job.model_validate(result) for result in results]

    def get_jobs_by_user(self, authority: Keystore, period: Optional[int] = None) -> List[Job]:
        results = self.get("job", parameters={'period': period} if period else None, with_authorisation_by=authority)
        return [Job.model_validate(result) for result in results]

    def get_job_status(self, job_id: str, with_authorisation_by: Keystore) -> JobStatus:
        result = self.get(f"job/{job_id}/status", with_authorisation_by=with_authorisation_by)
        return JobStatus.model_validate(result)

    def get_batch_status(self, batch_id: str, with_authorisation_by: Keystore) -> BatchStatus:
        result = self.get(f"batch/{batch_id}/status", with_authorisation_by=with_authorisation_by)
        return BatchStatus.model_validate(result)

    def cancel_job(self, job_id: str, with_authorisation_by: Keystore) -> JobStatus:
        result = self.delete(f"job/{job_id}/cancel", with_authorisation_by=with_authorisation_by)
        return JobStatus.model_validate(result)

    def purge_job(self, job_id: str, with_authorisation_by: Keystore) -> JobStatus:
        result = self.delete(f"job/{job_id}/purge", with_authorisation_by=with_authorisation_by)
        return JobStatus.model_validate(result)
