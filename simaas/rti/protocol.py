import os
import time
from typing import Optional, Tuple, Dict, Any

from pydantic import BaseModel

from simaas.core.errors import NetworkError, OperationError
from simaas.core.keystore import Keystore
from simaas.decorators import p2p_public_access, p2p_requires_authentication
from simaas.rti.schemas import Job, JobStatus, BatchStatus
from simaas.core.identity import Identity
from simaas.core.logging import get_logger
from simaas.dor.schemas import GitProcessorPointer
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress

log = get_logger('simaas.rti', 'rti')


class RunnerHandshakeRequest(BaseModel):
    runner_identity: Identity
    runner_address: str
    job_id: str
    gpp: GitProcessorPointer


class RunnerHandshakeResponse(BaseModel):
    job: Optional[Job]
    custodian_identity: Identity
    secrets: Dict[str, Optional[str]]
    join_batch: Optional[BatchStatus]

    def __repr__(self) -> str:
        return (f"RunnerHandshakeResponse(job={self.job!r}, "
                f"custodian_identity={self.custodian_identity!r}, "
                f"secrets=<{len(self.secrets)} redacted keys>, "
                f"join_batch={self.join_batch!r})")


@p2p_requires_authentication
class P2PRunnerPerformHandshake(P2PProtocol):
    NAME = 'rti-runner-handshake'

    def __init__(self, node) -> None:
        super().__init__(P2PRunnerPerformHandshake.NAME)
        self._node = node

    @classmethod
    def perform(
            cls, peer_address: P2PAddress, keystore: Keystore, runner_identity: Identity, runner_address: str,
            job_id: str, gpp: GitProcessorPointer, max_attempts: int = 3,
    ) -> Tuple[Optional[Job], Identity, Optional[str]]:
        for attempt in range(max_attempts):
            try:
                # send the request and a response
                response = p2p_request(
                    peer_address, cls.NAME, RunnerHandshakeRequest(
                        runner_identity=runner_identity, runner_address=runner_address, job_id=job_id, gpp=gpp
                    ), RunnerHandshakeResponse, with_authorisation_by=keystore,
                )
                response: RunnerHandshakeResponse = response[0]

                # set the secret environment variables (if any)
                for key, value in response.secrets.items():
                    if value is not None:
                        os.environ[key] = value

                return response.job, response.custodian_identity, response.join_batch

            except NetworkError:
                delay = attempt + 1
                log.warning('handshake', 'Failed to perform handshake with custodian, retrying', attempt=attempt+1, max_attempts=max_attempts, delay=delay)
                time.sleep(delay)

        raise OperationError(operation='handshake', cause=f'failed after {max_attempts} attempts')

    def handle(
            self, request: RunnerHandshakeRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        try:
            # based on job id, update the job with runner information and retrieve the job
            job: Job = self._node.rti.update_job(
                request.job_id, request.runner_identity, request.runner_address
            )

            # determine the secrets
            secrets: Dict[str, Optional[str]] = {}
            for key in request.gpp.proc_descriptor.required_secrets:
                secrets[key] = os.environ.get(key, None)

            # determine the batch status (if this job is part of one)
            batch_status: Optional[BatchStatus] = \
                self._node.rti.get_batch_status(job.batch_id) if job.batch_id else None

            return RunnerHandshakeResponse(
                job=job, custodian_identity=self._node.identity, secrets=secrets, join_batch=batch_status
            ), None

        except Exception as e:
            log.error('handshake', 'Handle handshake request failed', exc=e)

            return RunnerHandshakeResponse(
                job=None, custodian_identity=self._node.identity, secrets={}, join_batch=None
            ), None

    @staticmethod
    def request_type():
        return RunnerHandshakeRequest

    @staticmethod
    def response_type():
        return RunnerHandshakeResponse


class BatchBarrierRequest(BaseModel):
    barrier_name: str
    batch_status: BatchStatus


@p2p_public_access
# TODO(security): replace with a custodian-relayed release so this can be auth'd.
class BatchBarrier(P2PProtocol):
    NAME = 'rti-batch-barrier'

    def __init__(self, runner) -> None:
        super().__init__(BatchBarrier.NAME)
        self._runner = runner
        self._releases: Dict[str, dict] = {}
        # ``handle`` reads ``self._node`` via getattr in p2p_respond — point it
        # at the runner so identity lookups go through the runner's node db.
        self._node = getattr(runner, 'node', runner)

    @classmethod
    def perform(cls, peer_address: P2PAddress, keystore: Keystore, barrier_name: str,
                batch_status: BatchStatus) -> None:
        # ``keystore`` is kept in the signature for source compatibility, but
        # we don't present a client cert — BatchBarrier is @p2p_public_access
        # precisely because the receiving runner has no way to put us in its
        # trust bundle before receiving this very message (chicken-and-egg).
        # If we presented a cert, the receiver's TLS layer would reject it for
        # being unknown and we'd never reach the application handler.
        p2p_request(
            peer_address, cls.NAME, BatchBarrierRequest(
                barrier_name=barrier_name, batch_status=batch_status
            ), None,
        )

    def wait_for_release(self, barrier_name: str) -> Any:
        while barrier_name not in self._releases:
            # Check if the job has been interrupted
            if self._runner._interrupted:
                return None
            time.sleep(0.1)
        return self._releases.pop(barrier_name)

    def handle(
            self, request: BatchBarrierRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:

        # set the release content
        self._releases[request.barrier_name] = request.batch_status

        return None, None

    @staticmethod
    def request_type():
        return BatchBarrierRequest

    @staticmethod
    def response_type():
        return None


class JobStatusRequest(BaseModel):
    job_id: str
    job_status: JobStatus


@p2p_requires_authentication
class P2PPushJobStatus(P2PProtocol):
    NAME = 'rti-push-job-status'

    def __init__(self, node) -> None:
        super().__init__(P2PPushJobStatus.NAME)
        self._node = node
        self._rti = node.rti

    @classmethod
    def perform(
            cls, peer_address: P2PAddress, keystore: Keystore, job_id: str, job_status: JobStatus,
            max_attempts: int = 10,
    ) -> None:
        for attempt in range(max_attempts):
            try:
                p2p_request(
                    peer_address, cls.NAME, JobStatusRequest(job_id=job_id, job_status=job_status),
                    with_authorisation_by=keystore,
                )
                return None

            except NetworkError:
                time.sleep(0.5)

        raise OperationError(operation='push_job_status', cause=f'failed after {max_attempts} attempts')

    def handle(
            self, request: JobStatusRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None,
            identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self._rti.update_job_status(request.job_id, request.job_status)
        return None, None

    @staticmethod
    def request_type():
        return JobStatusRequest

    @staticmethod
    def response_type():
        return None


class InterruptJobRequest(BaseModel):
    ...


@p2p_requires_authentication
class P2PInterruptJob(P2PProtocol):
    NAME = 'rti-interrupt-job'

    def __init__(self, runner) -> None:
        super().__init__(P2PInterruptJob.NAME)
        self._runner = runner
        # ``handle`` reads ``self._node`` via getattr in p2p_respond — point it
        # at the runner so identity lookups go through the runner's node db.
        self._node = getattr(runner, 'node', runner)

    @classmethod
    def perform(cls, peer_address: P2PAddress, keystore: Keystore, max_attempts: int = 10) -> None:
        for attempt in range(max_attempts):
            try:
                p2p_request(
                    peer_address, cls.NAME, InterruptJobRequest(),
                    with_authorisation_by=keystore,
                )
                return None

            except NetworkError:
                time.sleep(0.5)

        raise OperationError(operation='interrupt_job', cause=f'failed after {max_attempts} attempts')

    def handle(
            self, request: InterruptJobRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None, identity: Optional[Identity] = None,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self._runner.on_job_cancel()
        return None, None

    @staticmethod
    def request_type():
        return InterruptJobRequest

    @staticmethod
    def response_type():
        return None
