import random
import string
import logging
import time

import pytest
from pydantic import BaseModel

from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.decorators import requires_authentication
from simaas.node.base import Node
from simaas.rest.exceptions import UnsuccessfulRequestError
from simaas.rest.proxy import EndpointProxy, get_proxy_prefix
from simaas.rest.schemas import EndpointDefinition

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)

endpoint_prefix = "/api/v1/test"


class TestResponse(BaseModel):
    __test__ = False

    key: str
    value: str


class TestDeleteRequest(BaseModel):
    __test__ = False

    key: str


class TestException(SaaSRuntimeException):
    __test__ = False

    pass


class TestRESTService:
    __test__ = False

    def __init__(self):
        self._objects = {}

    def endpoints(self) -> list:
        return [
            EndpointDefinition('POST', endpoint_prefix, 'create/{value}',
                               self.rest_post, TestResponse),

            EndpointDefinition('GET', endpoint_prefix, 'read/{key}',
                               self.rest_get, TestResponse),

            EndpointDefinition('PUT', endpoint_prefix, 'update/{key}/{value}',
                               self.rest_put, TestResponse),

            EndpointDefinition('DELETE', endpoint_prefix, 'delete/{key}',
                               self.rest_delete, TestResponse),

            EndpointDefinition('DELETE', endpoint_prefix, 'delete_body',
                               self.rest_delete_with_body, TestResponse),

            EndpointDefinition('DELETE', endpoint_prefix, 'delete_auth',
                               self.rest_delete_with_auth, TestResponse)
        ]

    def rest_post(self, value: str) -> TestResponse:
        key = None
        while key is None or key in self._objects:
            key = ''.join(random.choice(string.ascii_lowercase) for _ in range(8))

        self._objects[key] = value
        return TestResponse(key=key, value=self._objects[key])

    def rest_get(self, key: str) -> TestResponse:
        if key in self._objects:
            return TestResponse(key=key, value=self._objects[key])

        raise TestException("obj does not exist", details={
            'key': key
        })

    def rest_put(self, key: str, value: str) -> TestResponse:
        if key in self._objects:
            self._objects[key] = value
            return TestResponse(key=key, value=self._objects[key])

        raise TestException("obj does not exist", details={
            'key': key
        })

    def rest_delete(self, key: str) -> TestResponse:
        if key in self._objects:
            value = self._objects.pop(key)
            return TestResponse(key=key, value=value)

        raise TestException("obj does not exist", details={
            'key': key
        })

    def rest_delete_with_body(self, r: TestDeleteRequest) -> TestResponse:
        if r.key in self._objects:
            value = self._objects.pop(r.key)
            return TestResponse(key=r.key, value=value)

        raise TestException("obj does not exist", details={
            'key': r.key
        })

    @requires_authentication
    def rest_delete_with_auth(self, r: TestDeleteRequest) -> TestResponse:
        if r.key in self._objects:
            value = self._objects.pop(r.key)
            return TestResponse(key=r.key, value=value)

        raise TestException("obj does not exist", details={
            'key': r.key
        })


class TestProxy(EndpointProxy):
    __test__ = False

    def __init__(self, remote_address):
        EndpointProxy.__init__(self, get_proxy_prefix(endpoint_prefix), remote_address)

    def create(self, value: str) -> TestResponse:
        result = self.post(f"create/{value}")
        return TestResponse.model_validate(result)

    def read(self, key: str) -> TestResponse:
        result = self.get(f"read/{key}")
        return TestResponse.model_validate(result)

    def update(self, key: str, value: str) -> TestResponse:
        result = self.put(f"update/{key}/{value}")
        return TestResponse.model_validate(result)

    def remove(self, key: str) -> TestResponse:
        result = self.delete(f"delete/{key}")
        return TestResponse.model_validate(result)

    def remove_with_body(self, key: str) -> TestResponse:
        result = self.delete("delete_body", body={'key': key})
        return TestResponse.model_validate(result)

    def remove_with_auth(self, key: str, authority: Keystore = None) -> TestResponse:
        result = self.delete("delete_auth", body={'key': key}, with_authorisation_by=authority)
        return TestResponse.model_validate(result)


@pytest.fixture(scope='session')
def rest_node(test_context, session_keystore) -> Node:
    _node = test_context.get_node(session_keystore, enable_rest=True)
    rest_service = TestRESTService()

    _node.rest.add(rest_service.endpoints())
    time.sleep(5)

    return _node


@pytest.fixture(scope='session')
def rest_test_proxy(rest_node):
    proxy = TestProxy(rest_node.rest.address())
    return proxy


def test_create_read(rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    assert(result is not None)
    assert(result.value == 'hello world')

    result = rest_test_proxy.read(result.key)
    assert(result is not None)
    assert(result.value == 'hello world')


def test_update_ok(rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    assert(result is not None)
    assert(result.value == 'hello world')
    key = result.key

    result = rest_test_proxy.update(key, 'hello new world')
    assert(result is not None)
    assert(result.value == 'hello new world')


def test_update_fails(rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    assert(result is not None)
    assert(result.value == 'hello world')

    with pytest.raises(UnsuccessfulRequestError):
        rest_test_proxy.update('invalid', 'hello new world')


def test_delete_ok(rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    assert(result is not None)
    assert(result.value == 'hello world')
    key = result.key

    result = rest_test_proxy.remove(key)
    assert(result is not None)

    with pytest.raises(UnsuccessfulRequestError):
        rest_test_proxy.read(key)


def test_delete_fails(rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    assert(result is not None)
    assert(result.value == 'hello world')
    key = result.key

    with pytest.raises(UnsuccessfulRequestError):
        rest_test_proxy.remove('invalid_key')

    rest_test_proxy.read(key)


def test_delete_with_body(rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    key = result.key

    try:
        result = rest_test_proxy.remove_with_body(key)
        assert(result is not None)
    except Exception as e:
        print(e)

    with pytest.raises(UnsuccessfulRequestError):
        rest_test_proxy.read(key)


def test_delete_with_auth(test_context, rest_node, rest_test_proxy):
    result = rest_test_proxy.create('hello world')
    key = result.key

    good_authority = rest_node.keystore
    bad_authority = test_context.create_keystores(1)[0]

    with pytest.raises(UnsuccessfulRequestError) as e:
        # this should fail because the 'bad' authority is not known to the node
        rest_test_proxy.remove_with_auth(key, authority=bad_authority)
    assert e.value.details['reason'] == 'unknown identity'

    # this should succeed because the 'good' authority is known to the node
    result = rest_test_proxy.remove_with_auth(key, authority=good_authority)
    assert (result is not None)

    with pytest.raises(UnsuccessfulRequestError):
        rest_test_proxy.read(key)
