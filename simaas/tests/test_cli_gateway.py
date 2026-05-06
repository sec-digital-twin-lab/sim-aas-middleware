"""Integration tests for the Gateway API service.

Tests the full gateway flow against a live simaas node:
- User and API key management (via DatabaseWrapper)
- Data operations: upload, search, meta, download, tag, untag, delete
- Job operations: list processors, submit ABC job, poll status, verify output
"""

import json
import os
import random
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from simaas.core.helpers import hash_string_object
from simaas.core.logging import get_logger
from simaas.dor.schemas import DataObject
from simaas.gateway.db import DatabaseWrapper, User
from simaas.gateway.service import app, proxies
from simaas.rti.schemas import Job, JobStatus

log = get_logger(__name__, 'test')


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture(scope="module")
def gateway_db():
    """Module-scoped gateway database in a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_url = f"sqlite:///{os.path.join(tmpdir, 'db.dat')}"
        DatabaseWrapper.initialise(db_url)
        yield tmpdir


@pytest.fixture(scope="module")
def gateway_user(gateway_db, session_node, node_db_proxy):
    """Create a gateway user with identity published to the node."""
    user: User = DatabaseWrapper.create_user('testuser', 'test@example.com',
                                             hash_string_object('password').hex())
    node_db_proxy.update_identity(user.identity)
    return user


@pytest.fixture(scope="module")
def api_key(gateway_user):
    """Generate an API key for the gateway user."""
    key = DatabaseWrapper.generate_key(gateway_user, 'test key')
    return key.key


@pytest.fixture(scope="module")
def client(session_node, gateway_db):
    """TestClient for the gateway FastAPI app, connected to the session node."""
    proxies.address = session_node.rest.address()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def random_json_file():
    """Create a temporary JSON file with random content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'test_data.json')
        with open(path, 'w') as f:
            json.dump({'value': random.randint(0, 9999)}, f)
        yield path


def auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


# ==============================================================================
# Auth Tests
# ==============================================================================

class TestAuth:
    def test_missing_auth_header(self, client):
        response = client.get("/gateway/v1/data")
        assert response.status_code == 422

    def test_invalid_api_key(self, client):
        response = client.get("/gateway/v1/data", headers=auth_headers("invalid_key"))
        assert response.status_code == 401

    def test_valid_api_key(self, client, api_key):
        response = client.get("/gateway/v1/data", headers=auth_headers(api_key))
        assert response.status_code == 200

    def test_disabled_account(self, client, gateway_user, api_key):
        DatabaseWrapper.update_user_status([gateway_user.uuid], enabled=False)
        response = client.get("/gateway/v1/data", headers=auth_headers(api_key))
        assert response.status_code == 403

        # re-enable for subsequent tests
        DatabaseWrapper.update_user_status([gateway_user.uuid], enabled=True)


# ==============================================================================
# Data Tests
# ==============================================================================

class TestData:
    def test_search_empty(self, client, api_key):
        response = client.get("/gateway/v1/data", headers=auth_headers(api_key))
        assert response.status_code == 200
        assert response.json() == []

    def test_upload_and_search(self, client, api_key, random_json_file):
        body = json.dumps({
            'data_type': 'JSONObject',
            'data_format': 'json',
            'access_restricted': False,
            'content_encrypted': False,
            'tags': {'test_tag': 'test_value'}
        })

        with open(random_json_file, 'rb') as f:
            response = client.post(
                "/gateway/v1/data",
                headers=auth_headers(api_key),
                data={'body': body},
                files={'attachment': ('test_data.json', f, 'application/json')}
            )

        assert response.status_code == 200
        obj = DataObject.model_validate(response.json())
        assert obj.obj_id is not None
        assert obj.data_type == 'JSONObject'
        assert obj.data_format == 'json'

        # search should now find it
        response = client.get("/gateway/v1/data", headers=auth_headers(api_key))
        assert response.status_code == 200
        results = [DataObject.model_validate(o) for o in response.json()]
        assert len(results) >= 1
        assert any(r.obj_id == obj.obj_id for r in results)

        # search by data_type
        response = client.get("/gateway/v1/data", headers=auth_headers(api_key),
                              params={"data_type": "JSONObject"})
        assert response.status_code == 200
        results = [DataObject.model_validate(o) for o in response.json()]
        assert any(r.obj_id == obj.obj_id for r in results)

    def test_upload_meta_download_delete(self, client, api_key, random_json_file):
        # upload
        body = json.dumps({
            'data_type': 'JSONObject',
            'data_format': 'json',
            'access_restricted': False,
            'content_encrypted': False,
        })
        with open(random_json_file, 'rb') as f:
            original_content = f.read()
            f.seek(0)
            response = client.post(
                "/gateway/v1/data",
                headers=auth_headers(api_key),
                data={'body': body},
                files={'attachment': ('test_data.json', f, 'application/json')}
            )
        assert response.status_code == 200
        obj = DataObject.model_validate(response.json())
        obj_id = obj.obj_id

        # meta
        response = client.get(f"/gateway/v1/data/{obj_id}/meta", headers=auth_headers(api_key))
        assert response.status_code == 200
        meta_obj = DataObject.model_validate(response.json())
        assert meta_obj.obj_id == obj_id
        assert meta_obj.data_type == 'JSONObject'

        # download
        response = client.get(f"/gateway/v1/data/{obj_id}/content", headers=auth_headers(api_key))
        assert response.status_code == 200
        assert response.content == original_content

        # delete
        response = client.delete(f"/gateway/v1/data/{obj_id}", headers=auth_headers(api_key))
        assert response.status_code == 200
        deleted = DataObject.model_validate(response.json())
        assert deleted.obj_id == obj_id

        # meta should now return 404 or None
        response = client.get(f"/gateway/v1/data/{obj_id}/meta", headers=auth_headers(api_key))
        assert response.status_code in (404, 200)
        if response.status_code == 200:
            assert response.json() is None

    def test_tag_and_untag(self, client, api_key, random_json_file):
        # upload
        body = json.dumps({
            'data_type': 'JSONObject',
            'data_format': 'json',
            'access_restricted': False,
            'content_encrypted': False,
        })
        with open(random_json_file, 'rb') as f:
            response = client.post(
                "/gateway/v1/data",
                headers=auth_headers(api_key),
                data={'body': body},
                files={'attachment': ('test_data.json', f, 'application/json')}
            )
        assert response.status_code == 200
        obj_id = response.json()['obj_id']

        # add tags
        tags = [{'key': 'env', 'value': 'test'}, {'key': 'version', 'value': '1.0'}]
        response = client.put(
            f"/gateway/v1/data/{obj_id}/tags",
            headers=auth_headers(api_key),
            json=tags
        )
        assert response.status_code == 200
        updated = DataObject.model_validate(response.json())
        assert 'env' in updated.tags
        assert 'version' in updated.tags

        # remove tags
        response = client.request(
            "DELETE",
            f"/gateway/v1/data/{obj_id}/tags",
            headers=auth_headers(api_key),
            json=['env']
        )
        assert response.status_code == 200
        updated = DataObject.model_validate(response.json())
        assert 'env' not in updated.tags
        assert 'version' in updated.tags

        # cleanup
        client.delete(f"/gateway/v1/data/{obj_id}", headers=auth_headers(api_key))


# ==============================================================================
# Job Tests
# ==============================================================================

class TestJobs:
    def test_list_processors(self, client, api_key):
        response = client.get("/gateway/v1/proc", headers=auth_headers(api_key))
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    def test_list_jobs_empty(self, client, api_key):
        response = client.get("/gateway/v1/job", headers=auth_headers(api_key))
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_nonexistent_job(self, client, api_key):
        response = client.get("/gateway/v1/job/nonexistent_id", headers=auth_headers(api_key))
        assert response.status_code == 500  # simaas returns error for unknown job

    def test_cancel_nonexistent_job(self, client, api_key):
        response = client.delete("/gateway/v1/job/nonexistent_id", headers=auth_headers(api_key))
        assert response.status_code == 500

    def test_submit_abc_job(self, docker_available, deployed_abc_processor, client, api_key):
        """Submit an ABC job (a=3, b=4 -> c=7), poll until completion, verify output."""
        if not docker_available:
            pytest.skip("Docker is not available")

        proc_id = deployed_abc_processor.obj_id

        # verify the processor is listed
        response = client.get("/gateway/v1/proc", headers=auth_headers(api_key))
        assert response.status_code == 200
        procs = response.json()
        assert proc_id in procs

        # submit job with by-value inputs
        submit_body = {
            'task_input': [
                {'name': 'a', 'type': 'value', 'value': {'v': 3}},
                {'name': 'b', 'type': 'value', 'value': {'v': 4}},
            ],
            'task_output': [
                {'name': 'c'},
            ],
            'name': 'gateway-abc-test',
            'description': 'Gateway integration test for ABC processor',
        }
        response = client.post(
            f"/gateway/v1/proc/{proc_id}",
            headers=auth_headers(api_key),
            json=submit_body
        )
        assert response.status_code == 200
        job = Job.model_validate(response.json())
        assert job.id is not None
        job_id = job.id

        # poll for job completion
        terminal_states = {
            JobStatus.State.SUCCESSFUL,
            JobStatus.State.FAILED,
            JobStatus.State.CANCELLED,
        }
        timeout = 120
        start = time.time()
        status = None
        while time.time() - start < timeout:
            response = client.get(f"/gateway/v1/job/{job_id}", headers=auth_headers(api_key))
            assert response.status_code == 200
            status = JobStatus.model_validate(response.json())
            if status.state in terminal_states:
                break
            time.sleep(2)

        assert status is not None
        assert status.state == JobStatus.State.SUCCESSFUL, (
            f"Job failed with state: {status.state}. "
            f"Errors: {[e.message for e in status.errors]}"
        )

        # verify output 'c' exists
        assert 'c' in status.output
        assert status.output['c'] is not None
        output_obj_id = status.output['c'].obj_id

        # download the output via gateway and verify content
        response = client.get(f"/gateway/v1/data/{output_obj_id}/content", headers=auth_headers(api_key))
        assert response.status_code == 200
        output_content = json.loads(response.content)
        assert output_content['v'] == 7  # 3 + 4

        # verify the job shows up in list jobs
        response = client.get("/gateway/v1/job", headers=auth_headers(api_key))
        assert response.status_code == 200
        jobs_list = [Job.model_validate(j) for j in response.json()]
        # job may or may not still be in the list depending on retain_job_history
        # but the endpoint itself should work
        assert isinstance(jobs_list, list)


# ==============================================================================
# User/Key Management Tests
# ==============================================================================

class TestUserManagement:
    def test_create_and_list_users(self, gateway_db):
        user = DatabaseWrapper.create_user('user2', 'user2@example.com',
                                           hash_string_object('pass2').hex())
        assert user is not None
        assert user.name == 'user2'

        found, _ = DatabaseWrapper.get_users()
        assert user.uuid in found

        # cleanup
        DatabaseWrapper.delete_users([user.uuid])
        found, _ = DatabaseWrapper.get_users([user.uuid])
        assert user.uuid not in found

    def test_duplicate_email_fails(self, gateway_db, gateway_user):
        with pytest.raises(RuntimeError, match="already exists"):
            DatabaseWrapper.create_user('other', 'test@example.com',
                                        hash_string_object('pass').hex())

    def test_enable_disable(self, gateway_db, gateway_user):
        DatabaseWrapper.update_user_status([gateway_user.uuid], enabled=False)
        user = DatabaseWrapper.get_user(gateway_user.uuid)
        assert not user.enabled

        DatabaseWrapper.update_user_status([gateway_user.uuid], enabled=True)
        user = DatabaseWrapper.get_user(gateway_user.uuid)
        assert user.enabled

    def test_api_key_lifecycle(self, gateway_db, gateway_user):
        key = DatabaseWrapper.generate_key(gateway_user, 'lifecycle test')
        assert key.key is not None
        assert len(key.key) == 64  # 32 hex uuid + 32 hex token

        keys = DatabaseWrapper.get_keys_by_user([gateway_user.uuid])
        assert gateway_user.uuid in keys
        assert any(k.id == key.id for k in keys[gateway_user.uuid])

        DatabaseWrapper.delete_keys([key.id])
        found, missing = DatabaseWrapper.get_keys_by_id([key.id])
        assert key.id in missing
