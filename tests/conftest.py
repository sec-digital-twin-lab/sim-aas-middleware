import json
import logging
import os
import subprocess
import tempfile
import time

import pytest

from saas.cli.cmd_proc_builder import clone_repository
from saas.core.keystore import Keystore
from saas.dor.api import DORProxy
from saas.dor.schemas import ProcessorDescriptor, GitProcessorPointer, DataObject
from saas.helpers import determine_local_ip
from saas.node import Node, logger
from saas.nodedb.api import NodeDBProxy
from saas.rti.api import RTIProxy
from saas.rti.schemas import Processor
from tests.base_testcase import TestContext, update_keystore_from_credentials, PortMaster

commit_id = '330cf97c00ee0c66007cf2f0e0ebb38e7460d697'
ssh_key_path = os.path.join(os.environ['HOME'], 'Desktop', 'OneDrive', 'operations', 'ssh', 'id_testing')

# deactivate annoying DEBUG messages by multipart
logging.getLogger('multipart.multipart').setLevel(logging.WARNING)


@pytest.fixture(scope='session')
def test_context():
    context = TestContext()
    context.initialise()
    yield context
    context.cleanup()


@pytest.fixture(scope="session")
def docker_available():
    try:
        subprocess.run(['docker', 'info'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def keystore():
    with tempfile.TemporaryDirectory() as tempdir:
        _keystore = Keystore.new("keystore1", "no-email-provided", path=tempdir, password="password")
        update_keystore_from_credentials(_keystore)
        yield _keystore


@pytest.fixture(scope="session")
def temp_directory():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir


@pytest.fixture(scope="session")
def dor_proxy(node):
    proxy = DORProxy(node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def node_db_proxy(node):
    proxy = NodeDBProxy(node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def rti_proxy(node):
    proxy = RTIProxy(node.rest.address())
    return proxy


@pytest.fixture(scope="session")
def extra_keystores():
    keystores = []
    with tempfile.TemporaryDirectory() as tempdir:
        for i in range(3):
            keystore = Keystore.new(f"keystore-{i}", "no-email-provided", path=tempdir, password="password")
            keystores.append(keystore)
        yield keystores


@pytest.fixture(scope="session")
def node(keystore):
    with tempfile.TemporaryDirectory() as tempdir:
        local_ip = determine_local_ip()
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)

        _node = Node.create(keystore=keystore, storage_path=tempdir,
                            p2p_address=p2p_address, boot_node_address=p2p_address, rest_address=rest_address,
                            enable_dor=True, enable_rti=True, strict_deployment=False, job_concurrency=True,
                            retain_job_history=True)

        yield _node

        _node.shutdown()


@pytest.fixture(scope="session")
def exec_only_node(extra_keystores, node):
    with tempfile.TemporaryDirectory() as tempdir:
        local_ip = determine_local_ip()
        rest_address = PortMaster.generate_rest_address(host=local_ip)
        p2p_address = PortMaster.generate_p2p_address(host=local_ip)

        _node = Node.create(keystore=extra_keystores[1], storage_path=tempdir,
                            p2p_address=p2p_address, boot_node_address=p2p_address, rest_address=rest_address,
                            enable_dor=False, enable_rti=True, strict_deployment=False, job_concurrency=True,
                            retain_job_history=True)

        #  make exec-only node known to node
        _node.join_network(node.p2p.address())

        yield _node

        _node.shutdown()


def add_test_processor(dor: DORProxy, keystore: Keystore) -> DataObject:
    org = 'sec-digital-twin-lab'
    repo_name = 'saas-middleware'
    repo_url = f'https://github.com/{org}/{repo_name}'
    proc_name = 'example-processor'
    proc_path = 'examples/adapters/proc_example'
    image_name = f'{org}/{repo_name}/{proc_name}:{commit_id}'

    # does it exist in DOR? if not, build and add it
    result = dor.search(data_type='ProcessorDockerImage')
    existing = [obj for obj in result if obj.tags['image_name'] == image_name]
    if not existing:
        with tempfile.TemporaryDirectory() as tempdir:
            # clone the repository and checkout the specified commit
            repo_path = os.path.join(tempdir, 'repository')
            commit_timestamp = clone_repository(repo_url, repo_path, commit_id=commit_id)

            # read he processor descriptor
            descriptor_path = os.path.join(repo_path, proc_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                descriptor = ProcessorDescriptor.parse_obj(json.load(f))

            # store the GPP information in a file
            gpp_path = os.path.join(tempdir, 'gpp.json')
            with open(gpp_path, 'w') as f:
                gpp = GitProcessorPointer(repository=repo_url, commit_id=commit_id, proc_path=proc_path,
                                          proc_descriptor=descriptor)
                json.dump(gpp.dict(), f)

            # upload to DOR
            meta = dor.add_data_object(gpp_path, keystore.identity, False, False, 'ProcessorDockerImage', 'json',
                                       tags=[
                                           DataObject.Tag(key='repository', value=repo_url),
                                           DataObject.Tag(key='commit_id', value=commit_id),
                                           DataObject.Tag(key='commit_timestamp', value=commit_timestamp),
                                           DataObject.Tag(key='proc_path', value=proc_path),
                                           DataObject.Tag(key='proc_descriptor', value=descriptor.dict()),
                                           DataObject.Tag(key='image_name', value=image_name)
                                       ])
            os.remove(gpp_path)

            existing.append(meta)

    return existing[0]


@pytest.fixture(scope="session")
def deployed_test_processor(docker_available, rti_proxy, dor_proxy, node) -> DataObject:
    # add test processor
    meta = add_test_processor(dor_proxy, node.keystore)
    proc_id = meta.obj_id

    if not docker_available:
        yield meta

    else:
        # deploy it
        rti_proxy.deploy(proc_id, node.keystore)
        while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_DEPLOY:
            logger.info(f"Waiting for processor to be ready: {proc}")
            time.sleep(1)

        assert(rti_proxy.get_proc(proc_id).state == Processor.State.READY)
        logger.info(f"Processor deployed: {proc}")

        yield meta

        # undeploy it
        rti_proxy.undeploy(proc_id, node.keystore)
        try:
            while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_UNDEPLOY:
                logger.info(f"Waiting for processor to be ready: {proc}")
                time.sleep(1)
        except Exception as e:
            print(e)

        logger.info(f"Processor undeployed: {proc}")
