"""Unit tests for test helper utilities.

These tests verify the helper functions work correctly in isolation
without requiring external services.
"""

import json
import tempfile
from unittest.mock import MagicMock

import pytest

from simaas.tests.helper_waiters import (
    WaitTimeoutError,
    wait_for_condition,
    wait_for_job_completion,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
)
from simaas.tests.helper_factories import (
    TaskBuilder,
    create_abc_task,
    create_ping_task,
)
from simaas.tests.helper_assertions import (
    assert_job_successful,
    assert_job_failed,
    assert_job_cancelled,
    assert_data_object_content,
    assert_data_object_exists,
)
from simaas.rti.schemas import JobStatus, Processor


# =============================================================================
# Waiter Tests
# =============================================================================

class TestWaitForCondition:
    """Tests for wait_for_condition utility."""

    def test_wait_for_condition_immediate_success(self):
        """Test that wait_for_condition returns immediately when condition is true."""
        call_count = 0

        def condition():
            nonlocal call_count
            call_count += 1
            return True

        wait_for_condition(condition, timeout=5.0, poll_interval=0.1)
        assert call_count == 1

    def test_wait_for_condition_eventual_success(self):
        """Test that wait_for_condition waits until condition becomes true."""
        call_count = 0

        def condition():
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        wait_for_condition(condition, timeout=5.0, poll_interval=0.01)
        assert call_count == 3

    def test_wait_for_condition_timeout(self):
        """Test that wait_for_condition raises WaitTimeoutError on timeout."""
        def never_true():
            return False

        with pytest.raises(WaitTimeoutError) as exc_info:
            wait_for_condition(never_true, timeout=0.1, poll_interval=0.01, timeout_message="Custom timeout")

        assert "Custom timeout" in str(exc_info.value)


class TestWaitForJobCompletion:
    """Tests for wait_for_job_completion utility."""

    def test_wait_for_job_completion_successful(self):
        """Test waiting for a job that completes successfully."""
        mock_rti = MagicMock()
        mock_owner = MagicMock()

        # Simulate job completing after a few polls
        call_count = 0
        def get_status(job_id, owner):
            nonlocal call_count
            call_count += 1
            status = MagicMock(spec=JobStatus)
            if call_count < 3:
                status.state = JobStatus.State.RUNNING
            else:
                status.state = JobStatus.State.SUCCESSFUL
            return status

        mock_rti.get_job_status = get_status

        status = wait_for_job_completion(
            mock_rti, "job-123", mock_owner, timeout=5.0, poll_interval=0.01
        )

        assert status.state == JobStatus.State.SUCCESSFUL
        assert call_count >= 3

    def test_wait_for_job_completion_failed(self):
        """Test waiting for a job that fails."""
        mock_rti = MagicMock()
        mock_owner = MagicMock()

        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.FAILED
        mock_rti.get_job_status.return_value = status

        result = wait_for_job_completion(
            mock_rti, "job-123", mock_owner, timeout=5.0, poll_interval=0.01
        )

        assert result.state == JobStatus.State.FAILED

    def test_wait_for_job_completion_cancelled(self):
        """Test waiting for a job that gets cancelled."""
        mock_rti = MagicMock()
        mock_owner = MagicMock()

        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.CANCELLED
        mock_rti.get_job_status.return_value = status

        result = wait_for_job_completion(
            mock_rti, "job-123", mock_owner, timeout=5.0, poll_interval=0.01
        )

        assert result.state == JobStatus.State.CANCELLED


class TestWaitForProcessorReady:
    """Tests for wait_for_processor_ready utility."""

    def test_wait_for_processor_ready_success(self):
        """Test waiting for a processor that becomes ready."""
        mock_rti = MagicMock()

        # Simulate processor becoming ready after a few polls
        call_count = 0
        def get_proc(proc_id):
            nonlocal call_count
            call_count += 1
            proc = MagicMock(spec=Processor)
            if call_count < 3:
                proc.state = Processor.State.BUSY_DEPLOY
            else:
                proc.state = Processor.State.READY
            return proc

        mock_rti.get_proc = get_proc

        processor = wait_for_processor_ready(
            mock_rti, "proc-123", timeout=5.0, poll_interval=0.01
        )

        assert processor.state == Processor.State.READY
        assert call_count >= 3

    def test_wait_for_processor_ready_timeout(self):
        """Test timeout when processor never becomes ready."""
        mock_rti = MagicMock()

        proc = MagicMock(spec=Processor)
        proc.state = Processor.State.BUSY_DEPLOY
        mock_rti.get_proc.return_value = proc

        with pytest.raises(WaitTimeoutError) as exc_info:
            wait_for_processor_ready(mock_rti, "proc-123", timeout=0.1, poll_interval=0.01)

        assert "proc-123" in str(exc_info.value)


class TestWaitForProcessorUndeployed:
    """Tests for wait_for_processor_undeployed utility."""

    def test_wait_for_processor_undeployed_none(self):
        """Test that None processor is considered undeployed."""
        mock_rti = MagicMock()
        mock_rti.get_proc.return_value = None

        # Should complete without error
        wait_for_processor_undeployed(mock_rti, "proc-123", timeout=1.0, poll_interval=0.01)

    def test_wait_for_processor_undeployed_not_busy(self):
        """Test that non-BUSY_UNDEPLOY state is considered undeployed."""
        mock_rti = MagicMock()

        proc = MagicMock(spec=Processor)
        proc.state = Processor.State.READY
        mock_rti.get_proc.return_value = proc

        # Should complete without error
        wait_for_processor_undeployed(mock_rti, "proc-123", timeout=1.0, poll_interval=0.01)


# =============================================================================
# Factory Tests
# =============================================================================

class TestTaskBuilder:
    """Tests for TaskBuilder utility."""

    def test_task_builder_basic(self):
        """Test building a basic task."""
        task = (TaskBuilder("proc-123", "user-456")
                .with_input_value('a', {'v': 1})
                .with_output('c', 'user-456')
                .build())

        assert task.proc_id == "proc-123"
        assert task.user_iid == "user-456"
        assert len(task.input) == 1
        assert task.input[0].name == 'a'
        assert task.input[0].value == {'v': 1}
        assert len(task.output) == 1
        assert task.output[0].name == 'c'
        assert task.output[0].owner_iid == 'user-456'

    def test_task_builder_with_all_options(self):
        """Test building a task with all options."""
        task = (TaskBuilder("proc-123", "user-456")
                .with_name("Test Task")
                .with_description("A test task")
                .with_budget(vcpus=2, memory=2048)
                .with_namespace("test-ns")
                .with_input_value('a', {'v': 1})
                .with_input_reference('b', 'obj-789', user_signature='sig', c_hash='hash')
                .with_output('c', 'user-456', restricted_access=True, content_encrypted=True)
                .build())

        assert task.name == "Test Task"
        assert task.description == "A test task"
        assert task.budget.vcpus == 2
        assert task.budget.memory == 2048
        assert task.namespace == "test-ns"
        assert len(task.input) == 2
        assert len(task.output) == 1
        assert task.output[0].restricted_access is True
        assert task.output[0].content_encrypted is True


class TestCreateAbcTask:
    """Tests for create_abc_task factory."""

    def test_create_abc_task_defaults(self):
        """Test creating an ABC task with defaults."""
        mock_owner = MagicMock()
        mock_owner.identity.id = "owner-123"

        task = create_abc_task("proc-abc", mock_owner)

        assert task.proc_id == "proc-abc"
        assert task.user_iid == "owner-123"
        assert len(task.input) == 2

        # Find inputs by name
        input_a = next(i for i in task.input if i.name == 'a')
        input_b = next(i for i in task.input if i.name == 'b')

        assert input_a.value == {'v': 1}
        assert input_b.value == {'v': 1}

    def test_create_abc_task_custom_values(self):
        """Test creating an ABC task with custom values."""
        mock_owner = MagicMock()
        mock_owner.identity.id = "owner-123"

        task = create_abc_task("proc-abc", mock_owner, a=5, b=10, memory=2048, namespace="ns")

        input_a = next(i for i in task.input if i.name == 'a')
        input_b = next(i for i in task.input if i.name == 'b')

        assert input_a.value == {'v': 5}
        assert input_b.value == {'v': 10}
        assert task.budget.memory == 2048
        assert task.namespace == "ns"


class TestCreatePingTask:
    """Tests for create_ping_task factory."""

    def test_create_ping_task_defaults(self):
        """Test creating a Ping task with defaults."""
        mock_owner = MagicMock()
        mock_owner.identity.id = "owner-123"

        task = create_ping_task("proc-ping", mock_owner)

        assert task.proc_id == "proc-ping"
        input_msg = next(i for i in task.input if i.name == 'message')
        assert input_msg.value == {'content': 'ping'}

    def test_create_ping_task_custom_message(self):
        """Test creating a Ping task with custom message."""
        mock_owner = MagicMock()
        mock_owner.identity.id = "owner-123"

        task = create_ping_task("proc-ping", mock_owner, message="hello world")

        input_msg = next(i for i in task.input if i.name == 'message')
        assert input_msg.value == {'content': 'hello world'}


# =============================================================================
# Assertion Tests
# =============================================================================

class TestAssertJobSuccessful:
    """Tests for assert_job_successful utility."""

    def test_assert_job_successful_passes(self):
        """Test that assertion passes for successful job."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.SUCCESSFUL
        status.output = {'c': MagicMock()}
        status.errors = []

        # Should not raise
        assert_job_successful(status, expected_outputs=['c'])

    def test_assert_job_successful_fails_on_failed_state(self):
        """Test that assertion fails for non-successful state."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.FAILED
        status.errors = [MagicMock(message="Something went wrong")]

        with pytest.raises(AssertionError) as exc_info:
            assert_job_successful(status)

        assert "FAILED" in str(exc_info.value)

    def test_assert_job_successful_fails_on_missing_output(self):
        """Test that assertion fails when expected output is missing."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.SUCCESSFUL
        status.output = {'a': MagicMock()}
        status.errors = []

        with pytest.raises(AssertionError) as exc_info:
            assert_job_successful(status, expected_outputs=['c'])

        assert "'c' not found" in str(exc_info.value)


class TestAssertJobFailed:
    """Tests for assert_job_failed utility."""

    def test_assert_job_failed_passes(self):
        """Test that assertion passes for failed job."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.FAILED
        status.errors = [MagicMock(message="Error occurred")]

        assert_job_failed(status)

    def test_assert_job_failed_with_error_substring(self):
        """Test assertion with expected error substring."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.FAILED
        status.errors = [MagicMock(message="Connection timeout error")]

        assert_job_failed(status, expected_error_substring="timeout")

    def test_assert_job_failed_wrong_error(self):
        """Test that assertion fails when error doesn't contain expected substring."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.FAILED
        status.errors = [MagicMock(message="Connection error")]

        with pytest.raises(AssertionError) as exc_info:
            assert_job_failed(status, expected_error_substring="timeout")

        assert "timeout" in str(exc_info.value)


class TestAssertJobCancelled:
    """Tests for assert_job_cancelled utility."""

    def test_assert_job_cancelled_passes(self):
        """Test that assertion passes for cancelled job."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.CANCELLED

        assert_job_cancelled(status)

    def test_assert_job_cancelled_fails(self):
        """Test that assertion fails for non-cancelled job."""
        status = MagicMock(spec=JobStatus)
        status.state = JobStatus.State.SUCCESSFUL

        with pytest.raises(AssertionError) as exc_info:
            assert_job_cancelled(status)

        assert "cancelled" in str(exc_info.value).lower()


class TestAssertDataObjectContent:
    """Tests for assert_data_object_content utility."""

    def test_assert_data_object_content_passes(self):
        """Test that assertion passes when content matches."""
        mock_dor = MagicMock()
        mock_owner = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup mock to write expected content
            def mock_get_content(obj_id, owner, download_path):
                with open(download_path, 'w') as f:
                    json.dump({'v': 42, 'extra': 'data'}, f)

            mock_dor.get_content = mock_get_content

            # Should not raise
            assert_data_object_content(
                mock_dor, "obj-123", mock_owner, {'v': 42}, temp_dir
            )

    def test_assert_data_object_content_missing_key(self):
        """Test that assertion fails when key is missing."""
        mock_dor = MagicMock()
        mock_owner = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            def mock_get_content(obj_id, owner, download_path):
                with open(download_path, 'w') as f:
                    json.dump({'other': 'data'}, f)

            mock_dor.get_content = mock_get_content

            with pytest.raises(AssertionError) as exc_info:
                assert_data_object_content(
                    mock_dor, "obj-123", mock_owner, {'v': 42}, temp_dir
                )

            assert "'v' not found" in str(exc_info.value)

    def test_assert_data_object_content_wrong_value(self):
        """Test that assertion fails when value doesn't match."""
        mock_dor = MagicMock()
        mock_owner = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            def mock_get_content(obj_id, owner, download_path):
                with open(download_path, 'w') as f:
                    json.dump({'v': 100}, f)

            mock_dor.get_content = mock_get_content

            with pytest.raises(AssertionError) as exc_info:
                assert_data_object_content(
                    mock_dor, "obj-123", mock_owner, {'v': 42}, temp_dir
                )

            assert "Expected v=42" in str(exc_info.value)


class TestAssertDataObjectExists:
    """Tests for assert_data_object_exists utility."""

    def test_assert_data_object_exists_passes(self):
        """Test that assertion passes when object exists."""
        mock_dor = MagicMock()
        mock_obj = MagicMock()
        mock_obj.data_type = "JSONObject"
        mock_dor.get_meta.return_value = mock_obj

        assert_data_object_exists(mock_dor, "obj-123")

    def test_assert_data_object_exists_with_type(self):
        """Test assertion with expected data type."""
        mock_dor = MagicMock()
        mock_obj = MagicMock()
        mock_obj.data_type = "JSONObject"
        mock_dor.get_meta.return_value = mock_obj

        assert_data_object_exists(mock_dor, "obj-123", expected_data_type="JSONObject")

    def test_assert_data_object_exists_fails_when_missing(self):
        """Test that assertion fails when object doesn't exist."""
        mock_dor = MagicMock()
        mock_dor.get_meta.return_value = None

        with pytest.raises(AssertionError) as exc_info:
            assert_data_object_exists(mock_dor, "obj-123")

        assert "does not exist" in str(exc_info.value)

    def test_assert_data_object_exists_wrong_type(self):
        """Test that assertion fails when data type doesn't match."""
        mock_dor = MagicMock()
        mock_obj = MagicMock()
        mock_obj.data_type = "BinaryBlob"
        mock_dor.get_meta.return_value = mock_obj

        with pytest.raises(AssertionError) as exc_info:
            assert_data_object_exists(mock_dor, "obj-123", expected_data_type="JSONObject")

        assert "JSONObject" in str(exc_info.value)
