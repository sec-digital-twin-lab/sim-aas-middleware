import os
import shutil
import socket
import tempfile
import traceback

import pytest

from simaas.core.async_helpers import run_coro_safely
from docker.errors import ImageNotFound
from git import Repo

from simaas.cli.cmd_image import clone_repository, build_processor_image, PDIBuildLocal, PDIBuildGithub, PDIExport, \
    PDIImport, PDIMetaInformation

from simaas.core.helpers import get_timestamp_now

from simaas.core.errors import CLIError
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.schemas import DataObject
from simaas.helpers import find_available_port, docker_export_image
from simaas.tests.conftest import REPOSITORY_COMMIT_ID, REPOSITORY_URL, PROC_ABC_PATH

logger = Logging.get(__name__)
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir



def test_find_open_port():
    """Test find_available_port utility function."""
    # block port 5995
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('localhost', 5995))
    server_socket.listen(1)

    port = find_available_port(host='localhost', port_range=(5990, 5994))
    assert(port == 5990)

    port = find_available_port(host='localhost', port_range=(5995, 5999))
    assert(port == 5996)



def test_helper_image_clone_build_export(docker_available, session_node, temp_dir):
    """Test image helper functions for clone, build, and export workflows."""
    if not docker_available:
        pytest.skip("Docker is not available")


    # copy the repository (if required)
    repo_path = os.path.join(temp_dir, 'repository')
    if not os.path.isdir(repo_path):
        shutil.copytree(os.environ['SIMAAS_REPO_PATH'], repo_path)

    # get the current commit id
    repo = Repo(repo_path)
    commit_id = repo.head.commit.hexsha

    # -----
    # clone tests
    # -----

    try:
        clone_repository(REPOSITORY_URL+"_doesnt_exist", os.path.join(temp_dir, 'repository_doesnt_exist'),
                         credentials=(os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN']))
        assert False
    except CLIError:
        assert True

    try:
        clone_repository(REPOSITORY_URL, repo_path, commit_id="doesntexist", simulate_only=True)
        assert False
    except CLIError:
        assert os.path.isdir(repo_path)
        assert True

    try:
        clone_repository(REPOSITORY_URL, repo_path, commit_id=commit_id, simulate_only=True)
        assert True
    except CLIError:
        assert False

    # -----
    # build tests
    # -----
    image_name = 'test'

    try:
        build_processor_image(
            os.path.join(repo_path+"_wrong", PROC_ABC_PATH), os.environ['SIMAAS_REPO_PATH'], image_name
        )
        assert False
    except CLIError:
        assert True

    try:
        proc_path_wrong = "examples/adapters"
        build_processor_image(
            os.path.join(repo_path, proc_path_wrong), os.environ['SIMAAS_REPO_PATH'], image_name
        )
        assert False
    except CLIError:
        assert True

    try:
        build_processor_image(
            os.path.join(repo_path, PROC_ABC_PATH), os.environ['SIMAAS_REPO_PATH'], image_name
        )
    except CLIError:
        assert False

    # -----
    # export tests
    # -----
    image_path = os.path.join(temp_dir, 'image.tar')

    try:
        docker_export_image('doesnt-exist', image_path)
        assert False
    except ImageNotFound:
        assert True

    try:
        docker_export_image(image_name, image_path, keep_image=True)
        assert os.path.isfile(image_path)
    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
        print(trace)
        assert False



def test_cli_image_build_local(docker_available, temp_dir):
    """Test CLI image build from local processor source."""
    if not docker_available:
        pytest.skip("Docker is not available")

    # build the first time
    try:
        t0 = get_timestamp_now()

        # define arguments
        args = {
            'proc_path': os.path.join(examples_path, 'simple', 'abc'),
            'pdi_path': temp_dir,
            'force_build': True,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result
        meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'].endswith(f"{meta.proc_descriptor.name}_{meta.content_hash}.pdi")
        assert os.path.isfile(result['pdi_path'])

    except CLIError:
        assert False

    # build the second time
    try:
        t1 = get_timestamp_now()

        # define arguments
        args = {
            'proc_path': os.path.join(examples_path, 'simple', 'abc'),
            'pdi_path': temp_dir,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result
        meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'].endswith(f"{meta.proc_descriptor.name}_{meta.content_hash}.pdi")
        assert os.path.isfile(result['pdi_path'])

    except CLIError:
        assert False

    t2 = get_timestamp_now()

    # the second build attempt should be significantly faster (indicating that the existing image has been used)
    dt1 = t1 - t0
    dt2 = t2 - t1
    assert dt2 < dt1*0.1



def test_cli_image_build_github(docker_available, temp_dir):
    """Test CLI image build from GitHub repository."""
    if not docker_available:
        pytest.skip("Docker is not available")


    try:
        # define arguments
        args = {
            'repository': REPOSITORY_URL,
            'commit_id': REPOSITORY_COMMIT_ID,
            'proc_path': PROC_ABC_PATH,
            'pdi_path': temp_dir,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildGithub()
        result = cmd.execute(args)
        assert result is not None
        assert 'pdi_path' in result
        assert 'pdi_meta' in result
        meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'].endswith(f"{meta.proc_descriptor.name}_{meta.content_hash}.pdi")
        assert os.path.isfile(result['pdi_path'])

    except CLIError:
        assert False



def test_cli_image_export_import(docker_available, session_node, temp_dir):
    """Test CLI PDI export and import workflow."""
    if not docker_available:
        pytest.skip("Docker is not available")

    # create keystore
    password = 'password'
    keystore = Keystore.new('name', 'email', path=temp_dir, password=password)

    # ensure the node knows about this identity
    run_coro_safely(session_node.db.update_identity(keystore.identity))

    try:
        # define arguments
        args = {
            'proc_path': os.path.join(examples_path, 'simple', 'abc'),
            'pdi_path': temp_dir,
            'force_build': False,
            'keep_image': True,
            'arch': 'linux/amd64',
        }

        cmd = PDIBuildLocal()
        result = cmd.execute(args)
        assert result is not None

    except CLIError:
        assert False

    # -------
    # import tests
    # -------
    address = session_node.rest.address()
    pdi_path = result['pdi_path']

    try:
        args = {
            'pdi_path': 'location-does-not-exist',
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        cmd.execute(args)
        assert False

    except CLIError:
        assert True

    try:
        invalid_pdi_path = os.path.join(temp_dir, 'invalid.pdi')
        with open(invalid_pdi_path, 'wb') as f:
            f.write(b'skldfjghskpduhgspkdjfhgskdjfhgslkdfjhgsdkl;fjghsdfkljghsdflgjhdfgdsfgsdfgdsd')

        args = {
            'pdi_path': invalid_pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        cmd.execute(args)
        assert False

    except CLIError:
        assert True

    try:
        args = {
            'pdi_path': pdi_path,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIImport()
        result = cmd.execute(args)
        assert 'pdi' in result
        pdi0: DataObject = result['pdi']

    except CLIError:
        assert False

    # -------
    # export tests
    # -------

    try:
        args = {
            'obj_id': 'does not exist',
            'pdi_path': temp_dir,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIExport()
        cmd.execute(args)
        assert False

    except CLIError:
        assert True

    try:
        args = {
            'obj_id': pdi0.obj_id,
            'pdi_path': temp_dir,
            'address': f"{address[0]}:{address[1]}",
            'keystore-id': keystore.identity.id,
            'keystore': temp_dir,
            'password': password
        }

        cmd = PDIExport()
        result = cmd.execute(args)
        pdi_meta: PDIMetaInformation = result['pdi_meta']
        assert result['pdi_path'] == os.path.abspath(
            os.path.join(temp_dir, f"{pdi_meta.proc_descriptor.name}_{pdi_meta.content_hash}.pdi")
        )

    except CLIError:
        assert False


