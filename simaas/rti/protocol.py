import asyncio
import json
import threading
import time
from typing import Optional, Tuple

from pydantic import BaseModel

from simaas.p2p.exceptions import PeerUnavailableError
from simaas.rti.exceptions import RTIException
from simaas.rti.schemas import Job, JobStatus
from simaas.core.keystore import Keystore
from simaas.core.identity import Identity
from simaas.core.logging import Logging
from simaas.dor.schemas import GitProcessorPointer
from simaas.p2p.base import P2PProtocol, p2p_request, P2PAddress, P2PAttachment

logger = Logging.get('rti.protocol')


class RunnerHandshakeRequest(BaseModel):
    custodian: Identity
    custodian_address: str


class RunnerHandshakeResponse(BaseModel):
    payload: str


class P2PRunnerPerformHandshake(P2PProtocol):
    NAME = 'rti-runner-handshake'

    def __init__(self, runner) -> None:
        super().__init__(P2PRunnerPerformHandshake.NAME)
        self._mutex = threading.Lock()
        self._thread = None
        self._payload: Optional[str] = None
        self._runner = runner

    @classmethod
    async def perform(
            cls, p2p_address: str, custodian: Keystore, custodian_address: str, max_attempts: int = 10
    ) -> Tuple[GitProcessorPointer, Identity]:
        peer_address = P2PAddress(
            address=p2p_address,
            curve_secret_key=None,
            curve_public_key=None,
            curve_server_key=None
        )

        message = RunnerHandshakeRequest(
            custodian=custodian.identity,
            custodian_address=custodian_address
        )

        for attempt in range(max_attempts):
            try:
                # send the request and wait for the reply
                reply: Tuple[Optional[RunnerHandshakeResponse], Optional[P2PAttachment]] = await p2p_request(
                    peer_address, cls.NAME, message, reply_type=RunnerHandshakeResponse
                )
                reply: RunnerHandshakeResponse = reply[0]

                # the payload is encrypted -> manually decrypt it
                payload: bytes = reply.payload.encode('utf-8')
                payload: bytes = custodian.decrypt(payload)
                payload: str = payload.decode('utf-8')
                payload: dict = json.loads(payload)
                gpp: GitProcessorPointer = GitProcessorPointer.model_validate(payload['gpp'])
                runner: Identity = Identity.model_validate(payload['runner'])

                return gpp, runner

            except PeerUnavailableError:
                await asyncio.sleep(0.5)

        raise RTIException(f"Handshake failed after {max_attempts} attempts.")

    async def handle(
            self, request: RunnerHandshakeRequest, _: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
        # just in case there is multiple requests coming in
        with self._mutex:
            if self._thread is None:
                # prepare the payload, encrypted for this custodian
                payload: dict = {
                    'gpp': self._runner.gpp.model_dump(),
                    'runner': self._runner.identity.model_dump()
                }
                payload: str = json.dumps(payload)
                payload: bytes = payload.encode('utf-8')
                payload: bytes = request.custodian.encrypt(payload)
                self._payload: str = payload.decode('utf-8')

                # set the custodian with some delay to give time for handshake roundtrip to complete
                self._thread = threading.Thread(
                    target=self._update_custodian,
                    args=(request.custodian, request.custodian_address, 1.0)
                )
                self._thread.start()

        # respond with the required information
        return RunnerHandshakeResponse(payload=self._payload), None

    def _update_custodian(self, custodian: Identity, custodian_address: str, delay: float):
        time.sleep(delay)
        self._runner.on_custodian_update(custodian, custodian_address)

    @staticmethod
    def request_type():
        return RunnerHandshakeRequest

    @staticmethod
    def response_type():
        return RunnerHandshakeResponse


class PushJobRequest(BaseModel):
    job: Job


class P2PPushJob(P2PProtocol):
    NAME = 'rti-push-job'

    def __init__(self, runner) -> None:
        super().__init__(P2PPushJob.NAME)
        self._runner = runner

    @classmethod
    async def perform(cls, peer_address: P2PAddress, job: Job, max_attempts: int = 10) -> None:
        for attempt in range(max_attempts):
            try:
                await p2p_request(
                    peer_address, cls.NAME, PushJobRequest(job=job)
                )
                return None

            except PeerUnavailableError:
                await asyncio.sleep(0.5)

        raise RTIException(f"Uploading job input failed after {max_attempts} attempts.")

    async def handle(
            self, request: PushJobRequest, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
        self._runner.on_job_update(request.job)
        return None, None

    @staticmethod
    def request_type():
        return PushJobRequest

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
    async def perform(cls, peer_address: P2PAddress, job_id: str, job_status: JobStatus) -> None:
        await p2p_request(
            peer_address, cls.NAME, JobStatusRequest(job_id=job_id, job_status=job_status)
        )

    async def handle(
            self, request: JobStatusRequest, _: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
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

        raise RTIException(f"Uploading job input failed after {max_attempts} attempts.")

    async def handle(
            self, request: PushJobRequest, download_path: Optional[str] = None
    ) -> Tuple[Optional[BaseModel], Optional[P2PAttachment]]:
        self._runner.on_job_cancel()
        return None, None

    @staticmethod
    def request_type():
        return InterruptJobRequest

    @staticmethod
    def response_type():
        return None
