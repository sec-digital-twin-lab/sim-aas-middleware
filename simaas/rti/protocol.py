import asyncio
import os
import time
import traceback
from typing import Optional, Tuple, Dict, Any

from pydantic import BaseModel

from simaas.p2p.exceptions import PeerUnavailableError
from simaas.rti.exceptions import RTIException
from simaas.rti.schemas import Job, JobStatus, BatchStatus
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.dor.schemas import GitProcessorPointer
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress

logger = Logging.get('rti.protocol')


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


class P2PRunnerPerformHandshake(P2PProtocol):
    NAME = 'rti-runner-handshake'

    def __init__(self, node) -> None:
        super().__init__(P2PRunnerPerformHandshake.NAME)
        self._node = node

    @classmethod
    async def perform(
            cls, peer_address: P2PAddress, runner_identity: Identity, runner_address: str, job_id: str,
            gpp: GitProcessorPointer, max_attempts: int = 3
    ) -> Tuple[Optional[Job], Identity, Optional[str]]:
        for attempt in range(max_attempts):
            try:
                # send the request and await a response
                response = await p2p_request(
                    peer_address, cls.NAME, RunnerHandshakeRequest(
                        runner_identity=runner_identity, runner_address=runner_address, job_id=job_id, gpp=gpp
                    ), RunnerHandshakeResponse
                )
                response: RunnerHandshakeResponse = response[0]

                # set the secret environment variables (if any)
                for key, value in response.secrets.items():
                    if value is not None:
                        os.environ[key] = value

                return response.job, response.custodian_identity, response.join_batch

            except PeerUnavailableError:
                delay = attempt + 1
                logger.warning(f"Failed for perform handshake with custodian ({attempt+1}/{max_attempts}) -> "
                               f"Trying again in {delay} seconds...")
                await asyncio.sleep(delay)

        raise RTIException(f"Handshake with custodian failed after {max_attempts} attempts")

    async def handle(
            self, request: RunnerHandshakeRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        try:
            # based on job id, update the job with runner information and retrieve the job
            job: Job = await self._node.rti.update_job(
                request.job_id, request.runner_identity, request.runner_address
            )

            # determine the secrets
            secrets: Dict[str, Optional[str]] = {}
            for key in request.gpp.proc_descriptor.required_secrets:
                secrets[key] = os.environ.get(key, None)

            # determine the batch status (if this job is part of one)
            batch_status: Optional[BatchStatus] = \
                await self._node.rti.get_batch_status(job.batch_id) if job.batch_id else None

            return RunnerHandshakeResponse(
                job=job, custodian_identity=self._node.identity, secrets=secrets, join_batch=batch_status
            ), None

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"Handle handshake request failed: {e} {trace}")

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


class BatchBarrier(P2PProtocol):
    NAME = 'rti-batch-barrier'

    def __init__(self, runner) -> None:
        super().__init__(BatchBarrier.NAME)
        self._runner = runner
        self._releases: Dict[str, dict] = {}

    @classmethod
    async def perform(cls, peer_address: P2PAddress, barrier_name: str, batch_status: BatchStatus) -> None:
        await p2p_request(
            peer_address, cls.NAME, BatchBarrierRequest(
                barrier_name=barrier_name, batch_status=batch_status
            ), None
        )

    def wait_for_release(self, barrier_name: str) -> Any:
        while barrier_name not in self._releases:
            # Check if the job has been interrupted
            if self._runner._interrupted:
                return None
            time.sleep(0.1)
        return self._releases.pop(barrier_name)

    async def handle(
            self, request: BatchBarrierRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
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


class P2PPushJobStatus(P2PProtocol):
    NAME = 'rti-push-job-status'

    def __init__(self, node) -> None:
        super().__init__(P2PPushJobStatus.NAME)
        self._rti = node.rti

    @classmethod
    async def perform(
            cls, peer_address: P2PAddress, job_id: str, job_status: JobStatus, max_attempts: int = 10
    ) -> None:
        for attempt in range(max_attempts):
            try:
                await p2p_request(
                    peer_address, cls.NAME, JobStatusRequest(job_id=job_id, job_status=job_status)
                )
                return None

            except PeerUnavailableError:
                await asyncio.sleep(0.5)

        raise RTIException(f"Pushing job status failed after {max_attempts} attempts.")

    async def handle(
            self, request: JobStatusRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        await self._rti.update_job_status(request.job_id, request.job_status)
        return None, None

    @staticmethod
    def request_type():
        return JobStatusRequest

    @staticmethod
    def response_type():
        return None


class InterruptJobRequest(BaseModel):
    ...


class P2PInterruptJob(P2PProtocol):
    NAME = 'rti-interrupt-job'

    def __init__(self, runner) -> None:
        super().__init__(P2PInterruptJob.NAME)
        self._runner = runner

    @classmethod
    async def perform(cls, peer_address: P2PAddress, max_attempts: int = 10) -> None:
        for attempt in range(max_attempts):
            try:
                await p2p_request(
                    peer_address, cls.NAME, InterruptJobRequest()
                )
                return None

            except PeerUnavailableError:
                await asyncio.sleep(0.5)

        raise RTIException(f"Interrupting job input failed after {max_attempts} attempts.")

    async def handle(
            self, request: InterruptJobRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self._runner.on_job_cancel()
        return None, None

    @staticmethod
    def request_type():
        return InterruptJobRequest

    @staticmethod
    def response_type():
        return None
