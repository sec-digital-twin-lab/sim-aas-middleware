"""Wait utilities for polling conditions in tests.

This module provides utilities for waiting on asynchronous operations
such as job completion and processor readiness.
"""

import time
from typing import Callable, TypeVar

from simaas.core.keystore import Keystore
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import JobStatus, Processor


T = TypeVar('T')


class WaitTimeoutError(Exception):
    """Raised when a wait operation times out."""
    pass


def wait_for_condition(
    condition: Callable[[], bool],
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    timeout_message: str = "Condition not met within timeout"
) -> None:
    """
    Wait for a condition to become true.

    Args:
        condition: A callable that returns True when the condition is met.
        timeout: Maximum time to wait in seconds.
        poll_interval: Time between condition checks in seconds.
        timeout_message: Message to include in the exception if timeout occurs.

    Raises:
        WaitTimeoutError: If the condition is not met within the timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if condition():
            return
        time.sleep(poll_interval)
    raise WaitTimeoutError(timeout_message)


def wait_for_job_completion(
    rti_proxy: RTIProxy,
    job_id: str,
    owner: Keystore,
    timeout: float = 120.0,
    poll_interval: float = 1.0
) -> JobStatus:
    """
    Wait for a job to reach a terminal state.

    Args:
        rti_proxy: The RTI proxy to query for job status.
        job_id: The ID of the job to wait for.
        owner: The keystore of the job owner (for authorization).
        timeout: Maximum time to wait in seconds.
        poll_interval: Time between status checks in seconds.

    Returns:
        The final JobStatus once the job reaches a terminal state.

    Raises:
        WaitTimeoutError: If the job does not complete within the timeout.
    """
    terminal_states = {
        JobStatus.State.SUCCESSFUL,
        JobStatus.State.CANCELLED,
        JobStatus.State.FAILED
    }

    def is_complete() -> bool:
        status = rti_proxy.get_job_status(job_id, owner)
        return status.state in terminal_states

    wait_for_condition(
        is_complete,
        timeout,
        poll_interval,
        f"Job {job_id} did not complete within {timeout}s"
    )
    return rti_proxy.get_job_status(job_id, owner)


def wait_for_processor_ready(
    rti_proxy: RTIProxy,
    proc_id: str,
    timeout: float = 120.0,
    poll_interval: float = 1.0
) -> Processor:
    """
    Wait for a processor to become ready.

    Args:
        rti_proxy: The RTI proxy to query for processor state.
        proc_id: The ID of the processor to wait for.
        timeout: Maximum time to wait in seconds.
        poll_interval: Time between state checks in seconds.

    Returns:
        The Processor object once it reaches the READY state.

    Raises:
        WaitTimeoutError: If the processor does not become ready within the timeout.
    """
    def is_ready() -> bool:
        proc = rti_proxy.get_proc(proc_id)
        return proc is not None and proc.state == Processor.State.READY

    wait_for_condition(
        is_ready,
        timeout,
        poll_interval,
        f"Processor {proc_id} did not become ready within {timeout}s"
    )
    return rti_proxy.get_proc(proc_id)


def wait_for_processor_undeployed(
    rti_proxy: RTIProxy,
    proc_id: str,
    timeout: float = 120.0,
    poll_interval: float = 1.0
) -> None:
    """
    Wait for a processor to be fully undeployed.

    Args:
        rti_proxy: The RTI proxy to query for processor state.
        proc_id: The ID of the processor to wait for.
        timeout: Maximum time to wait in seconds.
        poll_interval: Time between state checks in seconds.

    Raises:
        WaitTimeoutError: If the processor is not undeployed within the timeout.
    """
    def is_undeployed() -> bool:
        proc = rti_proxy.get_proc(proc_id)
        # Processor is undeployed when it's either None or not in BUSY_UNDEPLOY state
        return proc is None or proc.state != Processor.State.BUSY_UNDEPLOY

    wait_for_condition(
        is_undeployed,
        timeout,
        poll_interval,
        f"Processor {proc_id} did not finish undeploying within {timeout}s"
    )
