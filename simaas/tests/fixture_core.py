"""Core test fixtures and utilities."""

import os
import shutil
import subprocess
import tempfile
import time
import traceback
from pathlib import Path
from typing import List

import pytest
from dotenv import load_dotenv

from simaas.core.async_helpers import run_coro_safely
from simaas.core.helpers import get_timestamp_now
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.helpers import PortMaster
from simaas.node.base import Node
from simaas.node.default import DefaultNode

# Import plugin classes
from simaas.plugins.builtins.dor_fs import FilesystemDORService
from simaas.plugins.builtins.rti_docker import DockerRTIService
from simaas.plugins.builtins.rti_aws.service import get_default_aws_config

load_dotenv()

REPOSITORY_URL = 'https://github.com/sec-digital-twin-lab/sim-aas-middleware'
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Constant tag for test images - tests always use local builds
CURRENT_COMMIT_ID = 'LOCAL_LATEST'

log = get_logger('tests.fixtures.core', 'test')


class TestContext:
    """Test context manager for creating and managing test resources."""

    def __init__(self):
        self._temp_testing_dir = os.path.join(os.environ['HOME'], 'testing')
        self.testing_dir = os.path.join(self._temp_testing_dir, str(get_timestamp_now()))
        self.host = "127.0.0.1"
        self.nodes = dict()
        self.proxies = dict()

    def initialise(self) -> None:
        """Initialize the test context by creating the testing directory."""
        try:
            os.makedirs(self.testing_dir)
        except OSError as e:
            raise Exception(f"path to working directory for testing '{self.testing_dir}' already exists!") from e

    def cleanup(self) -> None:
        """Clean up the test context."""
        for name in self.nodes:
            log.info(f"stopping node '{name}'")
            node = self.nodes[name]
            # Note: we skip leave_network and shutdown_rti for fast test cleanup
            # In production, call await node.leave_network() and await node.shutdown_rti() first
            node.shutdown()

        try:
            shutil.rmtree(self._temp_testing_dir)
        except OSError as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            log.error(f"exception during cleanup() -> {e} {trace}")

    def create_keystores(self, n: int) -> List[Keystore]:
        """Create n keystores."""
        keystores = []
        for i in range(n):
            keystore = Keystore.new(f"keystore_{i}", "no-email-provided", path=self.testing_dir, password=f"password_{i}")
            keystores.append(keystore)
        return keystores

    def generate_random_file(self, filename: str, size: int) -> str:
        """Generate a random file with the given size."""
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'wb') as f:
            f.write(os.urandom(int(size)))
        return path

    def create_file_with_content(self, filename: str, content: str) -> str:
        """Create a file with the given content."""
        path = os.path.join(self.testing_dir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def get_node(self, keystore: Keystore, enable_rest: bool = False,
                 dor_plugin_class: type = FilesystemDORService, rti_plugin_class: type = DockerRTIService,
                 retain_job_history: bool = True, strict_deployment: bool = False,
                 wd_path: str = None) -> Node:
        """Get or create a node for the given keystore."""
        name = keystore.identity.id
        if name in self.nodes:
            return self.nodes[name]

        p2p_address: str = PortMaster.generate_p2p_address(self.host)
        rest_address = PortMaster.generate_rest_address(self.host)

        storage_path = os.path.join(wd_path if wd_path else self.testing_dir, name)
        os.makedirs(storage_path, exist_ok=True)

        node = DefaultNode(keystore, storage_path, enable_db=True,
                           dor_plugin_class=dor_plugin_class, rti_plugin_class=rti_plugin_class,
                           retain_job_history=retain_job_history if rti_plugin_class is not None else None,
                           strict_deployment=strict_deployment if rti_plugin_class is not None else None)
        run_coro_safely(node.startup(p2p_address, rest_address=rest_address if enable_rest else None))

        import time
        time.sleep(2)

        self.nodes[name] = node

        return node


def generate_random_file(path: str, size: int) -> str:
    """Generate a random file at the given path with the given size."""
    with open(path, 'wb') as f:
        f.write(os.urandom(int(size)))
    return path


@pytest.fixture(scope='session')
def test_context():
    """Session-scoped fixture providing a TestContext instance."""
    context = TestContext()
    context.initialise()
    yield context
    context.cleanup()


@pytest.fixture(scope="session")
def docker_available():
    """Check if Docker is available on the system."""
    try:
        subprocess.run(['docker', 'info'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def aws_available():
    """Check if AWS credentials are configured."""
    return get_default_aws_config() is not None


@pytest.fixture(scope="session")
def session_keystore():
    """Session-scoped keystore."""
    with tempfile.TemporaryDirectory() as tempdir:
        _keystore = Keystore.new("keystore1", "no-email-provided", path=tempdir, password="password")
        yield _keystore


@pytest.fixture(scope="session")
def temp_directory():
    """Session-scoped temporary directory."""
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


@pytest.fixture(scope="session")
def extra_keystores():
    """Session-scoped list of additional keystores."""
    keystores = []
    with tempfile.TemporaryDirectory() as tempdir:
        for i in range(3):
            keystore = Keystore.new(f"keystore-{i}", "no-email-provided", path=tempdir, password="password")
            keystores.append(keystore)
        yield keystores
