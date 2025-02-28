import asyncio
import os
from typing import Optional, Tuple, Dict, List

from pydantic import BaseModel

from simaas.p2p.exceptions import PeerUnavailableError
from simaas.rti.exceptions import RTIException
from simaas.rti.schemas import Job, JobStatus
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


class P2PRunnerPerformHandshake(P2PProtocol):
    NAME = 'rti-runner-handshake'

    def __init__(self, node) -> None:
        super().__init__(P2PRunnerPerformHandshake.NAME)
        self._node = node

    @classmethod
    async def perform(
            cls, peer_address: P2PAddress, runner_identity: Identity, runner_address: str, job_id: str,
            gpp: GitProcessorPointer
    ) -> Tuple[Optional[Job], Identity]:
        response = await p2p_request(
            peer_address, cls.NAME, RunnerHandshakeRequest(
                runner_identity=runner_identity, runner_address=runner_address, job_id=job_id, gpp=gpp
            ), RunnerHandshakeResponse
        )
        response: RunnerHandshakeResponse = response[0]
        return response.job, response.custodian_identity

    async def handle(
            self, request: RunnerHandshakeRequest, attachment_path: Optional[str] = None,
            download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        # based on job id, update the job with runner information and retrieve the job (if any)
        job: Optional[Job] = self._node.rti.update_job(
            request.job_id, request.runner_identity, request.runner_address
        )

        # determine the secrets
        secrets: Dict[str, Optional[str]] = {}
        for secret in request.gpp.proc_descriptor.required_secrets:
            secrets[secret] = os.environ.get(secret, None)

        return RunnerHandshakeResponse(job=job, custodian_identity=self._node.identity, secrets=secrets), None

    @staticmethod
    def request_type():
        return RunnerHandshakeRequest

    @staticmethod
    def response_type():
        return RunnerHandshakeResponse


class JobStatusRequest(BaseModel):
    job_id: str
    job_status: JobStatus


class P2PPushJobStatus(P2PProtocol):
    NAME = 'rti-push-job-status'

    def __init__(self, node) -> None:
        super().__init__(P2PPushJobStatus.NAME)
        self._rti = node.rti

    @classmethod
    async def perform(cls, peer_address: P2PAddress, job_id: str, job_status: JobStatus) -> None:
        await p2p_request(
            peer_address, cls.NAME, JobStatusRequest(job_id=job_id, job_status=job_status)
        )

    async def handle(
            self, request: JobStatusRequest, attachment_path: Optional[str] = None, download_path: Optional[str] = None
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
