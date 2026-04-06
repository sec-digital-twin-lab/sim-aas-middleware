"""Knowledge Graph (kgraph) Processor integration tests.

Tests for the emissions processor, which computes emissions from electricity
consumption and emission rates, then imports results as RDF into a SPARQL database.

Requires Docker for Blazegraph container.
"""

import json
import logging
import os
import shutil
import tempfile
import time

import docker
import pytest
import requests

from examples.kgraph.emissions.processor import ProcessorEmissions, write_value
from simaas.core.logging import get_logger, initialise
from simaas.dor.wrappers import SPARQLWrapper
from simaas.helpers import determine_local_ip
from simaas.rti.schemas import JobStatus, Task
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.tests.fixture_core import BASE_DIR
from simaas.tests.fixture_mocks import DummyProgressListener
from simaas.tests.fixture_rti import PROC_EMISSIONS_PATH
from simaas.tests.helper_waiters import wait_for_job_completion
from simaas.tests.helper_assertions import assert_job_successful, assert_data_object_content

initialise(level=logging.DEBUG)
log = get_logger(__name__, 'test')

BLAZEGRAPH_IMAGE = 'lyrasis/blazegraph:2.1.5'
BLAZEGRAPH_INTERNAL_PORT = 8080
BLAZEGRAPH_HOST_PORT = 9999
BLAZEGRAPH_SPARQL_PATH = '/bigdata/sparql'


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture(scope='module')
def blazegraph():
    """Start a Blazegraph container for SPARQL testing. Module-scoped."""
    client = docker.from_env()

    container = client.containers.run(
        BLAZEGRAPH_IMAGE,
        detach=True,
        ports={f'{BLAZEGRAPH_INTERNAL_PORT}/tcp': BLAZEGRAPH_HOST_PORT},
        remove=True,
    )

    try:
        endpoint = f'http://localhost:{BLAZEGRAPH_HOST_PORT}{BLAZEGRAPH_SPARQL_PATH}'

        # wait for Blazegraph to be ready (up to 60s — slow under emulation on ARM)
        for _ in range(120):
            try:
                resp = requests.get(endpoint, params={'query': 'ASK { ?s ?p ?o }'},
                                    headers={'Accept': 'application/sparql-results+json'},
                                    timeout=2)
                if resp.status_code == 200:
                    break
            except requests.ConnectionError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError('Blazegraph did not become ready within 60 seconds')

        log.info('blazegraph', 'Blazegraph ready', endpoint=endpoint)
        yield endpoint

    finally:
        container.stop(timeout=5)
        client.close()


# ==============================================================================
# SPARQLWrapper unit tests
# ==============================================================================

@pytest.mark.integration
def test_sparql_wrapper_config_validation():
    """Test SPARQLWrapper.Config Pydantic model validation."""
    # valid config
    config = SPARQLWrapper.Config(endpoint='http://localhost:9999/blazegraph/sparql')
    assert config.endpoint == 'http://localhost:9999/blazegraph/sparql'
    assert config.timeout == 30
    assert config.username is None

    # valid config with all fields
    config = SPARQLWrapper.Config(
        endpoint='http://localhost:9999/blazegraph/sparql',
        update_endpoint='http://localhost:9999/blazegraph/sparql',
        username='admin',
        password='secret',
        default_graph='http://example.org/graph',
        timeout=60
    )
    assert config.username == 'admin'
    assert config.timeout == 60


@pytest.mark.integration
def test_sparql_wrapper_query_update(blazegraph):
    """Test basic SPARQL query and update operations via SPARQLWrapper."""
    config = SPARQLWrapper.Config(endpoint=blazegraph)

    with SPARQLWrapper(config, keep_history=True) as w:
        # insert a triple
        w.update('INSERT DATA { <http://example.org/s> <http://example.org/p> "hello" . }')

        # query it back
        result = w.query('SELECT ?o WHERE { <http://example.org/s> <http://example.org/p> ?o }')
        bindings = result['results']['bindings']
        assert len(bindings) == 1
        assert bindings[0]['o']['value'] == 'hello'

        # verify history
        history = w.history()
        assert len(history) == 2
        assert history[0].type == 'update'
        assert history[1].type == 'query'

        # cleanup
        w.update('DELETE DATA { <http://example.org/s> <http://example.org/p> "hello" . }')


@pytest.mark.integration
def test_sparql_wrapper_execute_auto_detect(blazegraph):
    """Test that execute() auto-detects query vs update operations."""
    config = SPARQLWrapper.Config(endpoint=blazegraph)

    with SPARQLWrapper(config) as w:
        # update detected
        w.execute('INSERT DATA { <http://example.org/x> <http://example.org/y> "z" . }')

        # query detected
        result = w.execute('ASK { <http://example.org/x> <http://example.org/y> ?o }')
        assert result is not None
        assert result['boolean'] is True

        # cleanup
        w.execute('DELETE DATA { <http://example.org/x> <http://example.org/y> "z" . }')


@pytest.mark.integration
def test_sparql_wrapper_import_graph(blazegraph):
    """Test importing an rdflib Graph via SPARQLWrapper."""
    from rdflib import Graph, URIRef, Literal

    config = SPARQLWrapper.Config(endpoint=blazegraph)

    g = Graph()
    ns = 'http://test.example.org/'
    g.add((URIRef(f'{ns}subject1'), URIRef(f'{ns}predicate1'), Literal('value1')))
    g.add((URIRef(f'{ns}subject2'), URIRef(f'{ns}predicate2'), Literal(42)))
    g.add((URIRef(f'{ns}subject3'), URIRef(f'{ns}predicate3'), Literal('value3')))

    with SPARQLWrapper(config) as w:
        w.import_graph(g, batch_size=2)

        # verify all 3 triples were imported
        result = w.query(f'SELECT (COUNT(*) as ?c) WHERE {{ ?s ?p ?o . FILTER(STRSTARTS(STR(?s), "{ns}")) }}')
        count = int(result['results']['bindings'][0]['c']['value'])
        assert count == 3

        # cleanup — Blazegraph doesn't support FILTER in DELETE WHERE
        for i in range(1, 4):
            w.update(f'DELETE WHERE {{ <{ns}subject{i}> <{ns}predicate{i}> ?o }}')


@pytest.mark.integration
def test_sparql_wrapper_history_disabled(blazegraph):
    """Test that history is not tracked when keep_history=False."""
    config = SPARQLWrapper.Config(endpoint=blazegraph)

    with SPARQLWrapper(config, keep_history=False) as w:
        w.update('INSERT DATA { <http://example.org/h> <http://example.org/h> "h" . }')
        assert w.history() is None
        w.update('DELETE DATA { <http://example.org/h> <http://example.org/h> "h" . }')


@pytest.mark.integration
def test_sparql_wrapper_context_manager(blazegraph):
    """Test that context manager properly closes the session."""
    config = SPARQLWrapper.Config(endpoint=blazegraph)

    wrapper = SPARQLWrapper(config)
    wrapper.__enter__()
    wrapper.__exit__(None, None, None)
    # session should be closed — subsequent requests will fail or create new connections


# ==============================================================================
# Processor local execution test
# ==============================================================================

@pytest.mark.integration
def test_processor_kgraph_local(dummy_namespace, blazegraph):
    """Test kgraph processor local execution with Blazegraph."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # prepare input files
        for src, dst in [
            ('electricity_consumption.json', 'electricity_consumption'),
            ('emission_rate.json', 'emission_rate')
        ]:
            shutil.copyfile(
                os.path.join(BASE_DIR, 'examples', 'kgraph', 'data', src),
                os.path.join(temp_dir, dst)
            )

        # write SPARQL config pointing to the Blazegraph fixture
        with open(os.path.join(temp_dir, 'kgdb_config'), 'w') as f:
            json.dump({
                'endpoint': blazegraph,
                'update_endpoint': blazegraph
            }, f)

        # run the processor
        status = JobStatus(
            state=JobStatus.State.INITIALISED,
            progress=0,
            output={},
            notes={},
            errors=[],
            message=None
        )
        proc_path = os.path.join(BASE_DIR, PROC_EMISSIONS_PATH)
        proc = ProcessorEmissions(proc_path)
        proc.run(
            temp_dir, None, DummyProgressListener(
                temp_dir, status, dummy_namespace.dor, expected_messages=None
            ), dummy_namespace, logging.getLogger('test.kgraph')
        )

        # verify output file
        c_path = os.path.join(temp_dir, 'calculated_emissions')
        assert os.path.isfile(c_path)
        with open(c_path, 'r') as f:
            result = json.load(f)
            # 100 * 0.412 = 41.2
            assert result['v'] == pytest.approx(41.2)

        # verify data was imported to Blazegraph
        config = SPARQLWrapper.Config(endpoint=blazegraph)
        with SPARQLWrapper(config) as w:
            result = w.query('SELECT (COUNT(*) as ?c) WHERE { ?s ?p ?o }')
            count = int(result['results']['bindings'][0]['c']['value'])
            assert count > 0, 'Expected RDF triples to be imported into Blazegraph'


# ==============================================================================
# Processor RTI job test
# ==============================================================================

@pytest.mark.integration
@pytest.mark.docker_only
def test_processor_kgraph_job(
        docker_available, test_context, session_node, dor_proxy, rti_proxy,
        deployed_emissions_processor, blazegraph
):
    """Test kgraph processor job execution via RTI with Blazegraph."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_emissions_processor.obj_id
    owner = session_node.keystore

    # upload input data objects
    ec_obj = dor_proxy.add_data_object(
        os.path.join(BASE_DIR, 'examples', 'kgraph', 'data', 'electricity_consumption.json'),
        owner.identity, False, False, 'JSONObject', 'json'
    )
    er_obj = dor_proxy.add_data_object(
        os.path.join(BASE_DIR, 'examples', 'kgraph', 'data', 'emission_rate.json'),
        owner.identity, False, False, 'JSONObject', 'json'
    )

    # build the SPARQL config using the host IP (reachable from inside the container)
    local_ip = determine_local_ip()
    sparql_endpoint = f'http://{local_ip}:{BLAZEGRAPH_HOST_PORT}{BLAZEGRAPH_SPARQL_PATH}'

    task = Task(
        proc_id=proc_id,
        user_iid=owner.identity.id,
        input=[
            Task.InputReference(name='electricity_consumption', type='reference',
                                obj_id=ec_obj.obj_id, user_signature=None, c_hash=None),
            Task.InputReference(name='emission_rate', type='reference',
                                obj_id=er_obj.obj_id, user_signature=None, c_hash=None),
            Task.InputValue(name='kgdb_config', type='value', value={
                'endpoint': sparql_endpoint,
                'update_endpoint': sparql_endpoint
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

    # wait for completion
    status = wait_for_job_completion(rti_proxy, job.id, owner)

    # verify job succeeded
    assert_job_successful(status, expected_outputs=['calculated_emissions'])
    assert_data_object_content(
        dor_proxy, status.output['calculated_emissions'].obj_id, owner,
        expected={'v': pytest.approx(41.2)}, temp_dir=test_context.testing_dir
    )
