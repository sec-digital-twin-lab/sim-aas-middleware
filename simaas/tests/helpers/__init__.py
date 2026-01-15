# Test helpers package
"""
Shared test utilities for the simaas test suite.

This package provides:
- waiters: Polling utilities for async operations
- factories: Test data creation helpers
- assertions: Custom assertion utilities
"""

from simaas.tests.helpers.waiters import (
    WaitTimeoutError,
    wait_for_condition,
    wait_for_job_completion,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
)

from simaas.tests.helpers.factories import (
    TaskBuilder,
    create_abc_task,
    create_ping_task,
)

from simaas.tests.helpers.assertions import (
    assert_job_successful,
    assert_job_failed,
    assert_job_cancelled,
    assert_data_object_content,
    assert_data_object_exists,
)

__all__ = [
    # waiters
    'WaitTimeoutError',
    'wait_for_condition',
    'wait_for_job_completion',
    'wait_for_processor_ready',
    'wait_for_processor_undeployed',
    # factories
    'TaskBuilder',
    'create_abc_task',
    'create_ping_task',
    # assertions
    'assert_job_successful',
    'assert_job_failed',
    'assert_job_cancelled',
    'assert_data_object_content',
    'assert_data_object_exists',
]
