"""Unit tests for simaas.core.errors module."""
import json
import logging

import pytest

from simaas.core.errors import (
    ConfigurationError,
    AuthenticationError,
    AuthorisationError,
    NotFoundError,
    NetworkError,
    OperationError,
    ValidationError,
    InternalError,
    RemoteError,
)
from simaas.core.errors import ExceptionContent
from simaas.core.logging import initialise


initialise(level=logging.DEBUG)


class TestBaseErrorBehavior:
    """Tests for common _BaseError functionality."""

    def test_id_auto_generation(self):
        """Test that IDs are auto-generated as 16-char alphanumeric strings."""
        e = NotFoundError(resource_type='test', resource_id='123')

        assert len(e.id) == 16
        assert e.id.isalnum()

    def test_id_uniqueness(self):
        """Test that each exception instance gets a unique ID."""
        e1 = NotFoundError(resource_type='test', resource_id='123')
        e2 = NotFoundError(resource_type='test', resource_id='123')

        assert e1.id != e2.id

    def test_details_storage(self):
        """Test that kwargs are stored in details dict."""
        e = NotFoundError(
            resource_type='data_object',
            resource_id='abc123',
            searched_locations=['local', 'remote']
        )

        assert e.details['resource_type'] == 'data_object'
        assert e.details['resource_id'] == 'abc123'
        assert e.details['searched_locations'] == ['local', 'remote']

    def test_hint_property(self):
        """Test that hint is accessible via property."""
        hint_text = 'Check if the service is running'
        e = NetworkError(peer_address='192.168.1.1', hint=hint_text)

        assert e.hint == hint_text
        assert e.details['hint'] == hint_text

    def test_hint_none_when_not_provided(self):
        """Test that hint is None when not provided."""
        e = NotFoundError(resource_type='test', resource_id='123')

        assert e.hint is None

    def test_json_serialization(self):
        """Test that details are JSON serializable."""
        e = ValidationError(
            field='email',
            expected='valid email format',
            actual='not-an-email',
            hint='Use format: user@domain.com'
        )

        serialized = json.dumps(e.details)
        deserialized = json.loads(serialized)

        assert deserialized['field'] == 'email'
        assert deserialized['expected'] == 'valid email format'
        assert deserialized['actual'] == 'not-an-email'
        assert deserialized['hint'] == 'Use format: user@domain.com'

    def test_json_fallback_for_non_serializable(self):
        """Test that non-JSON-serializable objects are converted to strings."""
        class CustomObject:
            def __str__(self):
                return 'CustomObject()'

        obj = CustomObject()
        e = InternalError(
            component='test_component',
            state={'custom': obj}
        )

        # The non-serializable object should be converted to string
        serialized = json.dumps(e.details)
        deserialized = json.loads(serialized)

        assert 'state' in deserialized
        # The nested dict should be preserved, with the custom object converted to string
        assert isinstance(deserialized['state'], dict)
        assert 'custom' in deserialized['state']
        assert 'CustomObject' in deserialized['state']['custom']

    def test_exception_content_compatibility(self):
        """Test that content property returns valid ExceptionContent."""
        e = OperationError(
            operation='download',
            stage='verification',
            cause='checksum mismatch'
        )

        content = e.content

        assert isinstance(content, ExceptionContent)
        assert content.id == e.id
        assert content.reason == e.reason
        assert content.details == e.details

    def test_exception_message(self):
        """Test that exception message is set to reason."""
        e = NotFoundError(resource_type='file', resource_id='test.txt')

        assert str(e) == e.reason


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_format_reason_full(self):
        """Test reason formatting with all parameters."""
        e = ConfigurationError(
            path='database.host',
            expected='hostname or IP',
            actual=None
        )

        assert "Configuration error at 'database.host'" in e.reason
        assert 'expected hostname or IP' in e.reason
        assert 'got None' in e.reason

    def test_format_reason_path_only(self):
        """Test reason formatting with path only."""
        e = ConfigurationError(path='api.key')

        assert e.reason == "Configuration error at 'api.key'"

    def test_format_reason_default_path(self):
        """Test reason formatting with no path."""
        e = ConfigurationError(expected='string', actual=123)

        assert "Configuration error at 'unknown'" in e.reason


class TestAuthenticationError:
    """Tests for AuthenticationError."""

    def test_format_reason_with_operation(self):
        """Test reason formatting with operation."""
        e = AuthenticationError(
            identity_id='user123',
            operation='login'
        )

        assert e.reason == "Authentication failed for 'user123' during 'login'"

    def test_format_reason_without_operation(self):
        """Test reason formatting without operation."""
        e = AuthenticationError(identity_id='user123')

        assert e.reason == "Authentication failed for 'user123'"


class TestAuthorisationError:
    """Tests for AuthorisationError."""

    def test_format_reason_full(self):
        """Test reason formatting with all parameters."""
        e = AuthorisationError(
            identity_id='user123',
            resource_id='doc456',
            required_permission='write'
        )

        assert "Authorisation denied for 'user123'" in e.reason
        assert "on resource 'doc456'" in e.reason
        assert "(requires 'write')" in e.reason

    def test_format_reason_minimal(self):
        """Test reason formatting with minimal parameters."""
        e = AuthorisationError(identity_id='user123')

        assert e.reason == "Authorisation denied for 'user123'"


class TestNotFoundError:
    """Tests for NotFoundError."""

    def test_format_reason_basic(self):
        """Test basic reason formatting."""
        e = NotFoundError(
            resource_type='data_object',
            resource_id='abc123'
        )

        assert e.reason == "data_object 'abc123' not found"

    def test_format_reason_with_locations_list(self):
        """Test reason formatting with searched locations as list."""
        e = NotFoundError(
            resource_type='file',
            resource_id='config.json',
            searched_locations=['/etc', '/home/user', '/tmp']
        )

        assert "file 'config.json' not found" in e.reason
        assert "(searched: /etc, /home/user, /tmp)" in e.reason

    def test_format_reason_with_locations_string(self):
        """Test reason formatting with searched locations as string."""
        e = NotFoundError(
            resource_type='file',
            resource_id='config.json',
            searched_locations='local cache'
        )

        assert "(searched: local cache)" in e.reason

    def test_with_hint(self):
        """Test NotFoundError with hint."""
        e = NotFoundError(
            resource_type='data_object',
            resource_id='abc123',
            hint='Object may exist on remote peer'
        )

        assert e.hint == 'Object may exist on remote peer'


class TestNetworkError:
    """Tests for NetworkError."""

    def test_format_reason_basic(self):
        """Test basic reason formatting."""
        e = NetworkError(peer_address='192.168.1.100')

        assert e.reason == "Network error with peer '192.168.1.100'"

    def test_format_reason_with_operation(self):
        """Test reason formatting with operation."""
        e = NetworkError(
            peer_address='192.168.1.100',
            operation='handshake'
        )

        assert e.reason == "Network error during 'handshake' with peer '192.168.1.100'"

    def test_format_reason_with_timeout(self):
        """Test reason formatting with timeout."""
        e = NetworkError(
            peer_address='192.168.1.100',
            operation='connect',
            timeout_ms=5000
        )

        assert "Network error during 'connect' with peer '192.168.1.100'" in e.reason
        assert "(timeout: 5000ms)" in e.reason


class TestOperationError:
    """Tests for OperationError."""

    def test_format_reason_basic(self):
        """Test basic reason formatting."""
        e = OperationError(operation='download')

        assert e.reason == "Operation 'download' failed"

    def test_format_reason_with_stage(self):
        """Test reason formatting with stage."""
        e = OperationError(
            operation='deploy',
            stage='validation'
        )

        assert e.reason == "Operation 'deploy' failed at stage 'validation'"

    def test_format_reason_with_cause(self):
        """Test reason formatting with cause."""
        e = OperationError(
            operation='upload',
            cause='disk full'
        )

        assert e.reason == "Operation 'upload' failed: disk full"

    def test_format_reason_full(self):
        """Test reason formatting with all parameters."""
        e = OperationError(
            operation='process',
            stage='parsing',
            cause='invalid format'
        )

        assert "Operation 'process' failed" in e.reason
        assert "at stage 'parsing'" in e.reason
        assert ": invalid format" in e.reason


class TestValidationError:
    """Tests for ValidationError."""

    def test_format_reason_full(self):
        """Test reason formatting with all parameters."""
        e = ValidationError(
            field='age',
            expected='positive integer',
            actual=-5
        )

        assert "Validation failed for field 'age'" in e.reason
        assert 'expected positive integer' in e.reason
        assert 'got -5' in e.reason

    def test_format_reason_field_only(self):
        """Test reason formatting with field only."""
        e = ValidationError(field='email')

        assert e.reason == "Validation failed for field 'email'"


class TestInternalError:
    """Tests for InternalError."""

    def test_format_reason_basic(self):
        """Test basic reason formatting."""
        e = InternalError(component='scheduler')

        assert e.reason == "Internal error in component 'scheduler'"

    def test_format_reason_with_state(self):
        """Test reason formatting with state."""
        e = InternalError(
            component='cache',
            state='inconsistent index'
        )

        assert e.reason == "Internal error in component 'cache': inconsistent index"

    def test_with_trace(self):
        """Test InternalError with trace information."""
        e = InternalError(
            component='worker',
            state='deadlock detected',
            trace='Thread-1 waiting for Thread-2'
        )

        assert e.details['trace'] == 'Thread-1 waiting for Thread-2'


class TestRemoteError:
    """Tests for RemoteError."""

    def test_format_reason_basic(self):
        """Test basic reason formatting."""
        e = RemoteError('Connection refused')

        assert e.reason == 'Connection refused'

    def test_format_reason_with_remote_id(self):
        """Test reason formatting with remote exception ID."""
        e = RemoteError(
            'Not found',
            remote_id='abc123',
            remote_details={'resource': 'doc456'}
        )

        assert e.reason == 'Not found'
        assert e.details['remote_id'] == 'abc123'
        assert e.details['remote_details'] == {'resource': 'doc456'}

    def test_format_reason_with_status_code(self):
        """Test reason formatting with HTTP status code."""
        e = RemoteError(
            'Server error',
            status_code=500
        )

        assert e.reason == 'Server error'
        assert e.details['status_code'] == 500


class TestExceptionRaisingAndCatching:
    """Tests for raising and catching exceptions."""

    def test_raise_and_catch_not_found(self):
        """Test raising and catching NotFoundError."""
        with pytest.raises(NotFoundError) as exc_info:
            raise NotFoundError(
                resource_type='data_object',
                resource_id='abc123',
                hint='Object may exist on remote peer'
            )

        e = exc_info.value
        assert 'abc123' in e.reason
        assert e.hint == 'Object may exist on remote peer'

    def test_catch_as_exception(self):
        """Test that new exceptions can be caught as generic Exception."""
        with pytest.raises(Exception):
            raise NetworkError(peer_address='localhost', operation='connect')

    def test_exception_inheritance(self):
        """Test that all exceptions inherit from Exception."""
        exceptions = [
            ConfigurationError(path='test'),
            AuthenticationError(identity_id='test'),
            AuthorisationError(identity_id='test'),
            NotFoundError(resource_type='test', resource_id='test'),
            NetworkError(peer_address='test'),
            OperationError(operation='test'),
            ValidationError(field='test'),
            InternalError(component='test'),
            RemoteError('test'),
        ]

        for e in exceptions:
            assert isinstance(e, Exception)
