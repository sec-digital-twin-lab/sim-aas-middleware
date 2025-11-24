import json
import logging
import os
import shutil
import tempfile
import time

import pytest

from examples.kgraph.proc.processor import ProcessorEmissions
from simaas.cli.cmd_proc_builder import build_processor_image
from simaas.core.helpers import get_timestamp_now
from simaas.core.logging import Logging
from simaas.dor.schemas import DataObject, ProcessorDescriptor, GitProcessorPointer
from simaas.helpers import docker_export_image, LOCAL_IP
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import JobStatus, Task, Processor
from simaas.tests.conftest import BASE_DIR, DummyProgressListener, REPOSITORY_COMMIT_ID

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


def test_proc_kgraph(dummy_namespace):
    with tempfile.TemporaryDirectory() as temp_dir:
        for src, dst in [
            ('electricity_consumption.json', 'electricity_consumption'),
            ('emission_rate.json', 'emission_rate')
        ]:
            shutil.copyfile(os.path.join(BASE_DIR, 'examples', 'kgraph', 'data', src), os.path.join(temp_dir, dst))

        with open(os.path.join(temp_dir, 'kgdb_config'), 'w') as f:
            json.dump({
                'endpoint': "http://localhost:9999/blazegraph/sparql",
                'update_endpoint': "http://localhost:9999/blazegraph/sparql"
            }, f, indent=2)

        # create the processor and run it
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'kgraph', 'proc')
        proc = ProcessorEmissions(proc_path)
        proc.run(
            temp_dir, None, DummyProgressListener(
                temp_dir, status, dummy_namespace.dor, expected_messages=None
            ), dummy_namespace, None
        )

        # read the result
        c_path = os.path.join(temp_dir, 'calculated_emissions')
        assert os.path.isfile(c_path)
        with open(c_path, 'r') as f:
            result: dict = json.load(f)
            value = result['v']
            # assert value == 3


@pytest.fixture(scope="session")
def deployed_processor(
        docker_available, github_credentials_available, rti_proxy, dor_proxy, session_node, session_data_dir
) -> DataObject:
    if not github_credentials_available:
        yield DataObject(
            obj_id='dummy',
            c_hash='dummy',
            data_type='dummy',
            data_format='dummy',
            created=DataObject.CreationDetails(timestamp=0, creators_iid=[]),
            owner_iid='dummy',
            access_restricted=False,
            access=[],
            tags={},
            last_accessed=0,
            custodian=None,
            content_encrypted=False,
            license=DataObject.License(by=False, sa=False, nc=False, nd=False),
            recipe=None
        )

    proc_name = 'proc-emissions'
    proc_path = 'examples/kgraph/proc'
    platform = 'linux/amd64'
    org = 'sec-digital-twin-lab'
    repo_name = 'sim-aas-middleware'
    repo_url = f'https://github.com/{org}/{repo_name}'
    image_name = f'{org}/{repo_name}/{proc_name}:{REPOSITORY_COMMIT_ID}'

    # does it exist in DOR? if not, build and add it
    result = dor_proxy.search(data_type='ProcessorDockerImage')
    existing = [obj for obj in result if obj.tags['image_name'] == image_name]
    if existing:
        meta = result[0]

    else:
        with tempfile.TemporaryDirectory() as tempdir:
            # don't clone the repo but use this repo (since it's sim-aas-middleware)
            repo_path = os.path.abspath(os.path.join(os.getcwd(), '..', '..'))

            # make full proc path
            abs_proc_path = os.path.join(repo_path, proc_path)

            # read the processor descriptor
            descriptor_path = os.path.join(abs_proc_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                # noinspection PyTypeChecker
                descriptor = ProcessorDescriptor.model_validate(json.load(f))

            # create the GPP descriptor
            gpp: GitProcessorPointer = GitProcessorPointer(
                repository=repo_url,
                commit_id=REPOSITORY_COMMIT_ID,
                proc_path=proc_path,
                proc_descriptor=descriptor
            )
            gpp_path = os.path.join(abs_proc_path, 'gpp.json')
            with open(gpp_path, 'w') as f:
                json.dump(gpp.model_dump(), f, indent=2)

            # get the credentials
            credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])

            # build the image
            build_processor_image(
                abs_proc_path, os.environ['SIMAAS_REPO_PATH'], image_name, credentials=credentials,
                platform=platform
            )

            # export the image
            image_path = os.path.join(tempdir, 'pdi.tar')
            docker_export_image(image_name, image_path)

            # upload to DOR
            meta = dor_proxy.add_data_object(
                image_path, session_node.keystore.identity, False, False, 'ProcessorDockerImage', 'tar', tags=[
                    DataObject.Tag(key='repository', value=gpp.repository),
                    DataObject.Tag(key='commit_id', value=gpp.commit_id),
                    DataObject.Tag(key='commit_timestamp', value=get_timestamp_now()),
                    DataObject.Tag(key='proc_path', value=gpp.proc_path),
                    DataObject.Tag(key='proc_descriptor', value=gpp.proc_descriptor.model_dump()),
                    DataObject.Tag(key='image_name', value=image_name)
                ])
            os.remove(gpp_path)

            existing.append(meta)

    if not docker_available:
        yield meta

    # deploy the processor
    proc_id = meta.obj_id
    rti_proxy.deploy(proc_id, session_node.keystore)

    while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_DEPLOY:
        logger.info(f"Waiting for processor to be ready: {proc}")
        time.sleep(1)

    assert(rti_proxy.get_proc(proc_id).state == Processor.State.READY)
    logger.info(f"Processor deployed: {proc}")

    yield meta

    # undeploy it
    rti_proxy.undeploy(proc_id, session_node.keystore)
    try:
        while (proc := rti_proxy.get_proc(proc_id)).state == Processor.State.BUSY_UNDEPLOY:
            logger.info(f"Waiting for processor to be ready: {proc}")
            time.sleep(1)
    except Exception as e:
        print(e)

    logger.info(f"Processor undeployed: {proc}")


def test_submit_list_get_job(
        docker_available, github_credentials_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_processor
):
    if not docker_available:
        pytest.skip("Docker is not available")

    if not github_credentials_available:
        pytest.skip("Github credentials not available")

    proc_id = deployed_processor.obj_id
    owner = session_node.keystore

    ec_obj = dor_proxy.add_data_object(
        os.path.join(BASE_DIR, 'examples', 'kgraph', 'data', 'electricity_consumption.json'),
        owner.identity, False, False, 'JSONObject', 'json'
    )

    er_obj = dor_proxy.add_data_object(
        os.path.join(BASE_DIR, 'examples', 'kgraph', 'data', 'emission_rate.json'),
        owner.identity, False, False, 'JSONObject', 'json'
    )

    # submit the task
    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputReference(name='electricity_consumption', type='reference', obj_id=ec_obj.obj_id,
                                user_signature=None, c_hash=None),
            Task.InputReference(name='emission_rate', type='reference', obj_id=er_obj.obj_id,
                                user_signature=None, c_hash=None),
            Task.InputValue(name='kgdb_config', type='value', value={
                'endpoint': f"http://{LOCAL_IP}:9999/blazegraph/sparql",
                'update_endpoint': f"http://{LOCAL_IP}:9999/blazegraph/sparql"
            }),
        ],
        output=[
            Task.Output.model_validate({
                'name': 'calculated_emissions',
                'owner_iid': owner.identity.id,
                'restricted_access': False,
                'content_encrypted': False,
                'target_node_iid': None
            })
        ],
        name=None,
        description=None,
        budget=ResourceDescriptor(vcpus=1, memory=1024),
        namespace=None
    )
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    job_id = job.id

    while True:
        try:
            status: JobStatus = rti_proxy.get_job_status(job_id, owner)

            from pprint import pprint
            pprint(status.model_dump())
            assert (status is not None)

            if status.state in [JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED]:
                break

        except Exception:
            pass

        time.sleep(1)

    # check if we have an object id for output object 'calculated_emissions'
    assert ('calculated_emissions' in status.output)

    # get the contents of the output data object
    download_path = os.path.join(test_context.testing_dir, 'calculated_emissions.json')
    dor_proxy.get_content(status.output['calculated_emissions'].obj_id, owner, download_path)
    assert (os.path.isfile(download_path))

    # read the result
    with open(download_path, 'r') as f:
        result: dict = json.load(f)
        print(json.dumps(result, indent=2))
