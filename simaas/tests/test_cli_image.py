import os
import shutil
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from simaas.core.async_helpers import run_coro_safely
from docker.errors import ImageNotFound
from git import Repo

from simaas.cli.cmd_image import clone_repository, build_processor_image, PDIBuildLocal, PDIBuildGithub, PDIExport, \
    PDIImport, PDIMetaInformation

from simaas.core.helpers import get_timestamp_now

from simaas.core.errors import CLIError
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.dor.schemas import DataObject
from simaas.helpers import docker_export_image
from simaas.tests.conftest import (
    CURRENT_COMMIT_ID, REPOSITORY_URL,
    PROC_ABC_PATH, PROC_PING_PATH, PROC_ROOM_PATH,
    PROC_THERMOSTAT_PATH, PROC_FACTORISATION_PATH, PROC_FACTOR_SEARCH_PATH,
    check_docker_image_exists,
)

log = get_logger(__name__, 'test')
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')

# All processors to build in Wave 0
ALL_PROCESSORS = [
    ('proc-abc', PROC_ABC_PATH),
    ('proc-ping', PROC_PING_PATH),
    ('proc-room', PROC_ROOM_PATH),
    ('proc-thermostat', PROC_THERMOSTAT_PATH),
    ('proc-factorisation', PROC_FACTORISATION_PATH),
    ('proc-factor-search', PROC_FACTOR_SEARCH_PATH),
]


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


def _get_image_name(proc_name: str) -> str:
    """Get the full Docker image name for a processor."""
    org = 'sec-digital-twin-lab'
    repo_name = 'sim-aas-middleware'
    return f'{org}/{repo_name}/{proc_name}:{CURRENT_COMMIT_ID}'


def _build_processor(proc_info: tuple, force_build: bool = True) -> dict:
    """Build a single processor image.

    Creates an isolated temp copy of the processor with its own gpp.json
    to avoid race conditions during parallel builds.

    Args:
        proc_info: Tuple of (proc_name, proc_path)
        force_build: If False, skip building if image already exists

    Returns:
        Dict with 'proc_name', 'success', 'skipped', and optional 'error' keys.
    """
    import json
    import shutil
    from simaas.dor.schemas import ProcessorDescriptor, GitProcessorPointer

    proc_name, proc_path = proc_info
    image_name = _get_image_name(proc_name)
    result = {'proc_name': proc_name, 'success': False, 'skipped': False, 'error': None}

    try:
        # Check if image already exists
        if not force_build and check_docker_image_exists(image_name):
            log.info(f"Image '{image_name}' already exists, skipping build")
            result['success'] = True
            result['skipped'] = True
            return result

        # Create isolated temp copy to avoid race conditions with parallel builds
        with tempfile.TemporaryDirectory() as tempdir:
            # Copy processor source to temp location
            abs_proc_path = os.path.join(repo_root_path, proc_path)
            temp_proc_path = os.path.join(tempdir, os.path.basename(proc_path))
            shutil.copytree(abs_proc_path, temp_proc_path)

            # Read the processor descriptor
            descriptor_path = os.path.join(temp_proc_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                descriptor = ProcessorDescriptor.model_validate(json.load(f))

            # Create GPP descriptor in the temp copy
            gpp = GitProcessorPointer(
                repository=REPOSITORY_URL,
                commit_id=CURRENT_COMMIT_ID,
                proc_path=proc_path,
                proc_descriptor=descriptor
            )
            gpp_path = os.path.join(temp_proc_path, 'gpp.json')
            with open(gpp_path, 'w') as f:
                json.dump(gpp.model_dump(), f, indent=2)

            # Build the image from the isolated temp copy
            build_processor_image(
                temp_proc_path, os.environ['SIMAAS_REPO_PATH'], image_name,
                credentials=(os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN']),
                platform='linux/amd64',
                force_build=force_build
            )

        result['success'] = True
        log.info(f"Successfully built image '{image_name}'")

    except Exception as e:
        result['error'] = str(e)
        log.error(f"Failed to build image '{image_name}': {e}")

    return result


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



def test_cli_image_build_github(docker_available, temp_dir):
    """Test CLI image build from GitHub repository."""
    if not docker_available:
        pytest.skip("Docker is not available")


    try:
        # define arguments
        args = {
            'repository': REPOSITORY_URL,
            'commit_id': CURRENT_COMMIT_ID,
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


def test_zz_build_all_processors(docker_available):
    """Wave 0 (opt-in): Rebuild all processor images in parallel.

    This test is opt-in - skip it to reuse existing cached images.
    When run, it rebuilds all PDIs from scratch (force_build=True).

    Uses ThreadPoolExecutor with 10 workers to build images concurrently.
    """
    if not docker_available:
        pytest.skip("Docker is not available")

    max_workers = 10
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_build_processor, proc_info): proc_info for proc_info in ALL_PROCESSORS}

        for future in as_completed(futures):
            proc_info = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({
                    'proc_name': proc_info[0],
                    'success': False,
                    'skipped': False,
                    'error': str(e)
                })

    # Report results
    built = [r for r in results if r['success'] and not r['skipped']]
    skipped = [r for r in results if r['skipped']]
    failed = [r for r in results if not r['success']]

    log.info(f"Build complete: {len(built)} built, {len(skipped)} skipped (cached), {len(failed)} failed")

    if failed:
        for r in failed:
            log.error(f"  Failed: {r['proc_name']}: {r['error']}")

    # Verify all built successfully
    assert all(r['success'] for r in results), f"Some processors failed to build: {[r['proc_name'] for r in failed]}"

