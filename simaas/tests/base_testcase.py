import os
import shutil
import time
import traceback

from multiprocessing import Lock
from typing import List

from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.helpers import get_timestamp_now, read_json_from_file, validate_json
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.core.schemas import SSHCredentials, GithubCredentials
from simaas.node.base import Node
from simaas.node.default import DefaultNode

logger = Logging.get('tests.base_testcase')


def update_keystore_from_credentials(keystore: Keystore, credentials_path: str = None) -> None:
    """
    Updates a keystore with credentials loaded from credentials file. This is a convenience function useful for
    testing purposes. A valid example content may look something like this:
    {
        "name": "John Doe",
        "email": "john.doe@internet.com",
        "ssh-credentials": [
            {
            "name": "my-remote-machine-A",
            "login": "johnd",
            "host": "10.8.0.1",
            "password": "super-secure-password-123"
            },
            {
            "name": "my-remote-machine-B",
            "login": "johnd",
            "host": "10.8.0.2",
            "key_path": "/home/johndoe/machine-b-key"
            }
        ],
        "github-credentials": [
            {
                "repository": "https://github.com/my-repo",
                "login": "JohnDoe",
                "personal_access_token": "ghp_xyz..."
            }
        ]
    }

    For SSH credentials note that you can either indicate a password or a path to a key file.

    :param keystore: the keystore that is to be updated
    :param credentials_path: the optional path to the credentials file (default is $HOME/.saas-credentials.json)
    :return:
    """

    credentials_schema = {
        'type': 'object',
        'properties': {
            'name': {'type': 'string'},
            'email': {'type': 'string'},
            'ssh-credentials': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string'},
                        'login': {'type': 'string'},
                        'host': {'type': 'string'},
                        'password': {'type': 'string'},
                        'key_path': {'type': 'string'}
                    },
                    'required': ['name', 'login', 'host']
                }
            },
            'github-credentials': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'repository': {'type': 'string'},
                        'login': {'type': 'string'},
                        'personal_access_token': {'type': 'string'}
                    },
                    'required': ['repository', 'login', 'personal_access_token']
                }
            }
        }
    }

    # load the credentials and validate
    path = credentials_path if credentials_path else os.path.join(os.environ['HOME'], '.saas-credentials.json')
    credentials = read_json_from_file(path)
    if not validate_json(content=credentials, schema=credentials_schema):
        raise SaaSRuntimeException("JSON validation failed", details={
            'instance': credentials,
            'schema': credentials_schema
        })

    # update profile (if applicable)
    keystore.update_profile(name=credentials['name'] if 'name' in credentials else None,
                            email=credentials['email'] if 'email' in credentials else None)

    # do we have SSH credentials?
    if 'ssh-credentials' in credentials:
        for item in credentials['ssh-credentials']:
            if 'key_path' in item:
                # read the ssh key from file
                with open(item['key_path'], 'r') as f:
                    ssh_key = f.read()

                keystore.ssh_credentials.update(item['name'],
                                                SSHCredentials(host=item['host'], login=item['login'],
                                                               key=ssh_key, passphrase=''))

            else:
                raise RuntimeError(f"Unexpected SSH credentials format: {item}")

        keystore.sync()

    # do we have Github credentials?
    if 'github-credentials' in credentials:
        for item in credentials['github-credentials']:
            keystore.github_credentials.update(item['repository'], GithubCredentials.parse_obj(item))
        keystore.sync()


class PortMaster:
    _mutex = Lock()
    _next_p2p = {}
    _next_rest = {}

    @classmethod
    def generate_p2p_address(cls, host: str = '127.0.0.1') -> (str, int):
        with cls._mutex:
            if host not in cls._next_p2p:
                cls._next_p2p[host] = 4100

            address = (host, cls._next_p2p[host])
            cls._next_p2p[host] += 1
            return address

    @classmethod
    def generate_rest_address(cls, host: str = '127.0.0.1') -> (str, int):
        with cls._mutex:
            if host not in cls._next_rest:
                cls._next_rest[host] = 5100

            address = (host, cls._next_rest[host])
            cls._next_rest[host] += 1
            return address

    @classmethod
    def generate_ws_address(cls, host: str = '127.0.0.1') -> (str, int):
        with cls._mutex:
            if host not in cls._next_rest:
                cls._next_rest[host] = 6100

            address = (host, cls._next_rest[host])
            cls._next_rest[host] += 1
            return address


class TestContext:
    def __init__(self):
        self._temp_testing_dir = os.path.join(os.environ['HOME'], 'testing')
        self.testing_dir = os.path.join(self._temp_testing_dir, str(get_timestamp_now()))
        self.host = "127.0.0.1"
        self.nodes = dict()
        self.proxies = dict()

    def initialise(self) -> None:
        # the testing directory gets deleted after the test is completed. if it already exists (unlikely) then
        # we abort in order not to end up deleting something that shouldn't be deleted.
        try:
            # create an empty working directory
            os.makedirs(self.testing_dir)
        except OSError as e:
            raise Exception(f"path to working directory for testing '{self.testing_dir}' already exists!") from e

    def cleanup(self) -> None:
        for name in self.nodes:
            logger.info(f"stopping node '{name}'")
            node = self.nodes[name]
            node.shutdown(leave_network=False)

        try:
            shutil.rmtree(self._temp_testing_dir)
        except OSError as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            logger.error(f"exception during cleanup() -> {e} {trace}")

    def create_keystores(self, n: int, use_credentials: bool = False) -> List[Keystore]:
        keystores = []
        for i in range(n):
            keystore = Keystore.new(f"keystore_{i}", "no-email-provided", path=self.testing_dir, password=f"password_{i}")
            keystores.append(keystore)

            # update keystore credentials (if applicable)
            if use_credentials:
                update_keystore_from_credentials(keystore)

        return keystores

    def create_nodes(self, keystores: List[Keystore], perform_join: bool = True, enable_rest: bool = False) -> List[Node]:
        nodes = []
        for i, keystore in enumerate(keystores):
            nodes.append(self.get_node(keystore, enable_rest=enable_rest))

            if perform_join and i > 0:
                nodes[i].join_network(nodes[0].p2p.address())
                time.sleep(2)

        return nodes

    def generate_random_file(self, filename: str, size: int) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'wb') as f:
            f.write(os.urandom(int(size)))
        return path

    def generate_zero_file(self, filename: str, size: int) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'wb') as f:
            f.write(b"\0" * int(size))
        return path

    def create_file_with_content(self, filename: str, content: str) -> str:
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def get_node(self, keystore: Keystore, enable_rest: bool = False,
                 use_dor: bool = True, use_rti: bool = True, retain_job_history: bool = True,
                 strict_deployment: bool = False, job_concurrency: bool = False, wd_path: str = None) -> Node:
        name = keystore.identity.id
        if name in self.nodes:
            return self.nodes[name]

        p2p_address = PortMaster.generate_p2p_address(self.host)
        rest_address = PortMaster.generate_rest_address(self.host)

        storage_path = os.path.join(wd_path if wd_path else self.testing_dir, name)
        os.makedirs(storage_path, exist_ok=True)

        # create node and startup services
        node = DefaultNode(keystore, storage_path, enable_db=True, enable_dor=use_dor, enable_rti=use_rti,
                           retain_job_history=retain_job_history if use_rti else None,
                           strict_deployment=strict_deployment if use_rti else None,
                           job_concurrency=job_concurrency if use_rti else None)
        node.startup(p2p_address, rest_address=rest_address if enable_rest else None)

        self.nodes[name] = node

        return node

    def resume_node(self, name: str, enable_rest: bool = False, use_dor: bool = True, use_rti: bool = True,
                    retain_job_history: bool = True, strict_deployment: bool = False) -> Node:
        if name in self.nodes:
            return self.nodes[name]

        else:
            p2p_address = PortMaster.generate_p2p_address(self.host)
            rest_address = PortMaster.generate_rest_address(self.host)

            storage_path = os.path.join(self.testing_dir, name)
            if not os.path.isdir(storage_path):
                raise RuntimeError(f"no storage path found to resume node at {storage_path}")

            # infer the keystore id
            keystore = None
            for filename in os.listdir(storage_path):
                if filename.endswith('.json') and len(filename) == 69:
                    keystore = Keystore.from_file(os.path.join(storage_path, filename), 'password')
                    break

            # create node and startup services
            node = DefaultNode(keystore, storage_path, enable_db=True, enable_dor=use_dor, enable_rti=use_rti,
                               retain_job_history=retain_job_history if use_rti else None,
                               strict_deployment=strict_deployment if use_rti else None,
                               job_concurrency=False)
            node.startup(p2p_address, rest_address=rest_address if enable_rest else None)

            self.nodes[name] = node
            return node


def generate_random_file(path: str, size: int) -> str:
    with open(path, 'wb') as f:
        f.write(os.urandom(int(size)))
    return path
