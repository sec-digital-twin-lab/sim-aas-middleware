from __future__ import annotations

import abc

from typing import List, Tuple, Optional, Union
from fastapi import Request

from simaas.rest.schemas import EndpointDefinition
from simaas.rest.exceptions import UnsuccessfulConnectionError
from simaas.rest.proxy import EndpointProxy, Session, get_proxy_prefix
from simaas.rest.auth import VerifyProcessorDeployed, VerifyUserIsNodeOwner, VerifyProcessorNotBusy, \
    VerifyAuthorisation, VerifyUserIsJobOwnerOrNodeOwner

from simaas.core.keystore import Keystore
from simaas.rti.schemas import Job, JobStatus, Processor, Task

RTI_ENDPOINT_PREFIX = "/api/v1/rti"
JOB_ENDPOINT_PREFIX = "/api/v1/job"


class RTIService(abc.ABC):
    def __init__(self, retain_job_history: bool, strict_deployment: bool, job_concurrency: bool):
        self._retain_job_history = retain_job_history
        self._strict_deployment = strict_deployment
        self._job_concurrency = job_concurrency

    @property
    def retain_job_history(self) -> bool:
        return self._retain_job_history

    @property
    def strict_deployment(self) -> bool:
        return self._strict_deployment

    @property
    def job_concurrency(self) -> bool:
        return self._job_concurrency

    def endpoints(self) -> List[EndpointDefinition]:
        return [
            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'proc',
                               self.get_all_procs, List[Processor], None),

            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}',
                               self.get_proc, Processor, [VerifyProcessorDeployed]),

            EndpointDefinition('POST', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}',
                               self.deploy, Processor,
                               [VerifyUserIsNodeOwner] if self._strict_deployment else []),

            EndpointDefinition('DELETE', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}',
                               self.undeploy, Processor,
                               [VerifyProcessorDeployed, VerifyProcessorNotBusy, VerifyUserIsNodeOwner] if
                               self._strict_deployment else [VerifyProcessorDeployed, VerifyProcessorNotBusy]),

            EndpointDefinition('POST', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}/jobs',
                               self.submit, Job, [VerifyProcessorDeployed, VerifyProcessorNotBusy,
                                                  VerifyAuthorisation]),

            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'proc/{proc_id}/jobs',
                               self.jobs_by_proc, List[Job], [VerifyProcessorDeployed]),

            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'job',
                               self.jobs_by_user, List[Job], [VerifyAuthorisation]),

            EndpointDefinition('GET', RTI_ENDPOINT_PREFIX, 'job/{job_id}/status',
                               self.get_job_status, JobStatus, [VerifyUserIsJobOwnerOrNodeOwner]),

            EndpointDefinition('PUT', RTI_ENDPOINT_PREFIX, 'job/{job_id}/status',
                               self.update_job_status, None, None),

            EndpointDefinition('DELETE', RTI_ENDPOINT_PREFIX, 'job/{job_id}/cancel',
                               self.job_cancel, JobStatus, [VerifyUserIsJobOwnerOrNodeOwner]),

            EndpointDefinition('DELETE', RTI_ENDPOINT_PREFIX, 'job/{job_id}/purge',
                               self.job_purge, JobStatus, [VerifyUserIsJobOwnerOrNodeOwner]),
        ]

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
    def deploy(self, proc_id: str) -> Processor:
        """
        Deploys a processor.
        """

    @abc.abstractmethod
    def undeploy(self, proc_id: str) -> Optional[Processor]:
        """
        Removes a processor from the RTI (if it exists).
        """

    @abc.abstractmethod
    def submit(self, proc_id: str, task: Task, request: Request) -> Job:
        """
        Submits a task to a deployed processor, thereby creating a new job. Authorisation is required by the owner
        of the task/job.
        """

    @abc.abstractmethod
    def jobs_by_proc(self, proc_id: str) -> List[Job]:
        """
        Retrieves a list of active jobs processed by a processor. Any job that is pending execution or actively
        executed will be included in the list.
        """

    @abc.abstractmethod
    def jobs_by_user(self, request: Request) -> List[Job]:
        """
        Retrieves a list of active jobs by a user. If the user is the node owner, all active jobs will be returned.
        """

    @abc.abstractmethod
    def update_job_status(self, job_id: str, job_status: JobStatus) -> None:
        """
        Updates the status of a particular job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """

    @abc.abstractmethod
    def get_job_owner_iid(self, job_id: str) -> str:
        ...

    @abc.abstractmethod
    def get_job_status(self, job_id: str) -> JobStatus:
        """
        Retrieves detailed information about the status of a job. Authorisation is required by the owner of the job
        (i.e., the user that has created the job by submitting the task in the first place).
        """

    @abc.abstractmethod
    def job_cancel(self, job_id: str) -> JobStatus:
        """
        Attempts to cancel a running job. Depending on the implementation of the processor, this may or may not be
        possible.
        """

    @abc.abstractmethod
    def job_purge(self, job_id: str) -> JobStatus:
        """
        Purges a running job. It will be removed regardless of its state.
        """


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
        return [Processor.parse_obj(result) for result in results]

    def get_proc(self, proc_id: str) -> Processor:
        result = self.get(f"proc/{proc_id}")
        return Processor.parse_obj(result)

    def deploy(self, proc_id: str, authority: Keystore) -> Processor:
        result = self.post(f"proc/{proc_id}", with_authorisation_by=authority)
        return Processor.parse_obj(result)

    def undeploy(self, proc_id: str, authority: Keystore) -> Processor:
        result = self.delete(f"proc/{proc_id}", with_authorisation_by=authority)
        return Processor.parse_obj(result)

    def submit_job(self, proc_id: str, job_input: List[Union[Task.InputReference, Task.InputValue]],
                   job_output: List[Task.Output], with_authorisation_by: Keystore, name: str = None,
                   description: str = None) -> Job:

        # build the body
        body = {
            'proc_id': proc_id,
            'input': [i.dict() for i in job_input],
            'output': [o.dict() for o in job_output],
            'user_iid': with_authorisation_by.identity.id,
            'name': name,
            'description': description
        }

        # post the request
        result = self.post(f"proc/{proc_id}/jobs", body=body, with_authorisation_by=with_authorisation_by)

        return Job.parse_obj(result)

    def get_jobs_by_proc(self, proc_id: str) -> List[Job]:
        results = self.get(f"proc/{proc_id}/jobs")
        return [Job.parse_obj(result) for result in results]

    def get_jobs_by_user(self, authority: Keystore, period: Optional[int] = None) -> List[Job]:
        results = self.get("job", parameters={'period': period} if period else None, with_authorisation_by=authority)
        return [Job.parse_obj(result) for result in results]

    def get_job_status(self, job_id: str, with_authorisation_by: Keystore) -> JobStatus:
        result = self.get(f"job/{job_id}/status", with_authorisation_by=with_authorisation_by)
        return JobStatus.parse_obj(result)

    def update_job_status(self, job_id: str, status: JobStatus) -> None:
        self.put(f"job/{job_id}/status", body=status.dict())

    def cancel_job(self, job_id: str, with_authorisation_by: Keystore) -> JobStatus:
        result = self.delete(f"job/{job_id}/cancel", with_authorisation_by=with_authorisation_by)
        return JobStatus.parse_obj(result)

    def purge_job(self, job_id: str, with_authorisation_by: Keystore) -> JobStatus:
        result = self.delete(f"job/{job_id}/purge", with_authorisation_by=with_authorisation_by)
        return JobStatus.parse_obj(result)


class JobRESTProxy(EndpointProxy):
    def __init__(self, remote_address: (str, int), credentials: (str, str) = None,
                 endpoint_prefix: Tuple[str, str] = get_proxy_prefix(JOB_ENDPOINT_PREFIX)):
        super().__init__(endpoint_prefix, remote_address, credentials=credentials)

    def job_status(self) -> Optional[JobStatus]:
        try:
            result = self.get("status")
            return JobStatus.parse_obj(result)
        except UnsuccessfulConnectionError:
            # the server might be down already (e.g., if job is done or failed) or not yet up (in case the job has
            # not been started yet).
            return None

    def job_cancel(self) -> Optional[JobStatus]:
        try:
            result = self.put("cancel")
            return JobStatus.parse_obj(result)
        except UnsuccessfulConnectionError:
            # the server might be down already (e.g., if job is done or failed) or not yet up (in case the job has
            # not been started yet).
            return None
        except Exception as e:
            print(e)
