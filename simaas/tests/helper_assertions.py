"""Custom assertion helpers for tests.

This module provides assertion utilities that encapsulate common
verification patterns used across tests.
"""

import json
import os
from typing import Any, Dict, List, Optional

from simaas.core.keystore import Keystore
from simaas.dor.api import DORProxy
from simaas.rti.schemas import JobStatus


def assert_job_successful(
    status: JobStatus,
    expected_outputs: Optional[List[str]] = None
) -> None:
    """Assert that a job completed successfully with expected outputs."""
    assert status.state == JobStatus.State.SUCCESSFUL, (
        f"Job failed with state: {status.state}. "
        f"Errors: {[e.message for e in status.errors]}"
    )
    if expected_outputs:
        for output_name in expected_outputs:
            assert output_name in status.output, (
                f"Expected output '{output_name}' not found in job outputs. "
                f"Available outputs: {list(status.output.keys())}"
            )
            assert status.output[output_name] is not None, (
                f"Output '{output_name}' exists but is None"
            )


def assert_job_failed(
    status: JobStatus,
    expected_error_substring: Optional[str] = None
) -> None:
    """Assert that a job failed."""
    assert status.state == JobStatus.State.FAILED, (
        f"Expected job to fail but got state: {status.state}"
    )
    if expected_error_substring:
        error_messages = [e.message for e in status.errors]
        all_errors = " ".join(error_messages)
        assert expected_error_substring in all_errors, (
            f"Expected error containing '{expected_error_substring}' "
            f"but got: {error_messages}"
        )


def assert_job_cancelled(status: JobStatus) -> None:
    """Assert that a job was cancelled."""
    assert status.state == JobStatus.State.CANCELLED, (
        f"Expected job to be cancelled but got state: {status.state}"
    )


def assert_data_object_content(
    dor_proxy: DORProxy,
    obj_id: str,
    owner: Keystore,
    expected: Dict[str, Any],
    temp_dir: str
) -> None:
    """Assert that a data object contains expected content."""
    download_path = os.path.join(temp_dir, f'{obj_id}.json')
    dor_proxy.get_content(obj_id, owner, download_path)

    with open(download_path, 'r') as f:
        content = json.load(f)

    for key, value in expected.items():
        assert key in content, (
            f"Key '{key}' not found in content. "
            f"Available keys: {list(content.keys())}"
        )
        assert content[key] == value, (
            f"Expected {key}={value}, got {key}={content[key]}"
        )


def assert_data_object_exists(
    dor_proxy: DORProxy,
    obj_id: str,
    expected_data_type: Optional[str] = None
) -> None:
    """Assert that a data object exists in the DOR."""
    obj = dor_proxy.get_meta(obj_id)
    assert obj is not None, f"Data object {obj_id} does not exist"

    if expected_data_type:
        assert obj.data_type == expected_data_type, (
            f"Expected data type '{expected_data_type}', "
            f"got '{obj.data_type}'"
        )
