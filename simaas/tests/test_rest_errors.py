"""Unit tests for REST exception handlers in simaas.rest.service."""

import pytest
from fastapi.testclient import TestClient

from simaas.core.errors import (
    AuthenticationError,
    AuthorisationError,
    NotFoundError,
    ValidationError,
    NetworkError,
    ConfigurationError,
    OperationError,
    InternalError,
)
from simaas.rest.service import RESTApp


@pytest.fixture
def app():
    """Create a RESTApp instance for testing."""
    return RESTApp()


@pytest.fixture
def client(app):
    """Create a TestClient for the REST app."""
    return TestClient(app.api, raise_server_exceptions=False)


class TestAuthenticationErrorHandler:
    """Tests for AuthenticationError handler (401)."""

    def test_returns_401_status(self, app, client):
        """Test that AuthenticationError returns 401 status code."""
        @app.api.get("/test-auth-error")
        async def raise_auth_error():
            raise AuthenticationError(identity_id='user123', operation='login')

        response = client.get("/test-auth-error")

        assert response.status_code == 401

    def test_response_body_structure(self, app, client):
        """Test that response contains id, reason, and details."""
        @app.api.get("/test-auth-error-body")
        async def raise_auth_error():
            raise AuthenticationError(identity_id='user123', operation='login')

        response = client.get("/test-auth-error-body")
        body = response.json()

        assert 'id' in body
        assert 'reason' in body
        assert 'details' in body
        assert body['details']['identity_id'] == 'user123'
        assert body['details']['operation'] == 'login'


class TestAuthorisationErrorHandler:
    """Tests for AuthorisationError handler (403)."""

    def test_returns_403_status(self, app, client):
        """Test that AuthorisationError returns 403 status code."""
        @app.api.get("/test-authz-error")
        async def raise_authz_error():
            raise AuthorisationError(
                identity_id='user123',
                resource_id='doc456',
                required_permission='write'
            )

        response = client.get("/test-authz-error")

        assert response.status_code == 403

    def test_response_body_structure(self, app, client):
        """Test that response contains correct details."""
        @app.api.get("/test-authz-error-body")
        async def raise_authz_error():
            raise AuthorisationError(
                identity_id='user123',
                resource_id='doc456',
                required_permission='write'
            )

        response = client.get("/test-authz-error-body")
        body = response.json()

        assert body['details']['identity_id'] == 'user123'
        assert body['details']['resource_id'] == 'doc456'
        assert body['details']['required_permission'] == 'write'


class TestNotFoundErrorHandler:
    """Tests for NotFoundError handler (404)."""

    def test_returns_404_status(self, app, client):
        """Test that NotFoundError returns 404 status code."""
        @app.api.get("/test-not-found")
        async def raise_not_found():
            raise NotFoundError(resource_type='data_object', resource_id='abc123')

        response = client.get("/test-not-found")

        assert response.status_code == 404

    def test_response_body_structure(self, app, client):
        """Test that response contains correct details."""
        @app.api.get("/test-not-found-body")
        async def raise_not_found():
            raise NotFoundError(
                resource_type='data_object',
                resource_id='abc123',
                searched_locations=['local', 'remote']
            )

        response = client.get("/test-not-found-body")
        body = response.json()

        assert body['details']['resource_type'] == 'data_object'
        assert body['details']['resource_id'] == 'abc123'
        assert body['details']['searched_locations'] == ['local', 'remote']


class TestValidationErrorHandler:
    """Tests for ValidationError handler (422)."""

    def test_returns_422_status(self, app, client):
        """Test that ValidationError returns 422 status code."""
        @app.api.get("/test-validation")
        async def raise_validation():
            raise ValidationError(
                field='email',
                expected='valid email format',
                actual='not-an-email'
            )

        response = client.get("/test-validation")

        assert response.status_code == 422

    def test_response_body_structure(self, app, client):
        """Test that response contains correct details."""
        @app.api.get("/test-validation-body")
        async def raise_validation():
            raise ValidationError(
                field='email',
                expected='valid email format',
                actual='not-an-email'
            )

        response = client.get("/test-validation-body")
        body = response.json()

        assert body['details']['field'] == 'email'
        assert body['details']['expected'] == 'valid email format'
        assert body['details']['actual'] == 'not-an-email'


class TestNetworkErrorHandler:
    """Tests for NetworkError handler (502)."""

    def test_returns_502_status(self, app, client):
        """Test that NetworkError returns 502 status code."""
        @app.api.get("/test-network")
        async def raise_network():
            raise NetworkError(peer_address='192.168.1.100', operation='connect')

        response = client.get("/test-network")

        assert response.status_code == 502

    def test_response_body_structure(self, app, client):
        """Test that response contains correct details."""
        @app.api.get("/test-network-body")
        async def raise_network():
            raise NetworkError(
                peer_address='192.168.1.100',
                operation='connect',
                timeout_ms=5000
            )

        response = client.get("/test-network-body")
        body = response.json()

        assert body['details']['peer_address'] == '192.168.1.100'
        assert body['details']['operation'] == 'connect'
        assert body['details']['timeout_ms'] == 5000


class TestBaseErrorCatchAll:
    """Tests for _BaseError catch-all handler (500)."""

    def test_configuration_error_returns_500(self, app, client):
        """Test that ConfigurationError returns 500 via catch-all."""
        @app.api.get("/test-config")
        async def raise_config():
            raise ConfigurationError(path='database.host', expected='hostname', actual=None)

        response = client.get("/test-config")

        assert response.status_code == 500

    def test_operation_error_returns_500(self, app, client):
        """Test that OperationError returns 500 via catch-all."""
        @app.api.get("/test-operation")
        async def raise_operation():
            raise OperationError(operation='deploy', stage='validation', cause='timeout')

        response = client.get("/test-operation")

        assert response.status_code == 500

    def test_internal_error_returns_500(self, app, client):
        """Test that InternalError returns 500 via catch-all."""
        @app.api.get("/test-internal")
        async def raise_internal():
            raise InternalError(component='scheduler', state='deadlock detected')

        response = client.get("/test-internal")

        assert response.status_code == 500

    def test_response_body_structure(self, app, client):
        """Test that catch-all response contains correct structure."""
        @app.api.get("/test-catchall-body")
        async def raise_operation():
            raise OperationError(operation='deploy', stage='validation')

        response = client.get("/test-catchall-body")
        body = response.json()

        assert 'id' in body
        assert 'reason' in body
        assert 'details' in body
        assert body['details']['operation'] == 'deploy'
        assert body['details']['stage'] == 'validation'


class TestResponseIdFormat:
    """Tests for exception ID format in responses."""

    def test_id_is_16_char_alphanumeric(self, app, client):
        """Test that response ID is 16 characters alphanumeric."""
        @app.api.get("/test-id-format")
        async def raise_error():
            raise NotFoundError(resource_type='test', resource_id='123')

        response = client.get("/test-id-format")
        body = response.json()

        assert len(body['id']) == 16
        assert body['id'].isalnum()
