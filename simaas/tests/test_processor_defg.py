"""DEFG Processor integration tests.

Tests for the DEFG processor which has all-optional inputs and outputs,
verifying local execution across all 16 I/O combinations, Docker-based
RTI execution, validation edge cases, and provenance tracking.
"""

import json
import logging
import os
import tempfile

import pytest

from examples.simple.defg.processor import ProcessorDEFG, write_value
from simaas.core.errors import RemoteError
from simaas.core.helpers import generate_random_string, get_timestamp_now
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger, initialise
from simaas.dor.schemas import DataObject
from simaas.nodedb.schemas import NodeInfo
from simaas.rti.schemas import Task, Job, JobStatus
from simaas.tests.fixture_core import BASE_DIR
from simaas.tests.fixture_mocks import DummyProgressListener
from simaas.tests.helper_factories import create_defg_task, TaskBuilder
from simaas.tests.helper_waiters import wait_for_job_completion
from simaas.tests.helper_assertions import assert_job_successful, assert_data_object_content

initialise(level=logging.DEBUG)
log = get_logger(__name__, 'test')

_dummy_keystore = Keystore.new('defg-test-dummy')


# ==============================================================================
# Helper: build a minimal Job for local tests
# ==============================================================================

def _make_local_job(include_f: bool, include_g: bool) -> Job:
    """Create a minimal Job object for local processor testing.

    The local processor only inspects job.task.output to determine which
    outputs are declared. Everything else is stubbed.
    """
    outputs = []
    if include_f:
        outputs.append(Task.Output(
            name='f', owner_iid='test', restricted_access=False,
            content_encrypted=False, target_node_iid=None
        ))
    if include_g:
        outputs.append(Task.Output(
            name='g', owner_iid='test', restricted_access=False,
            content_encrypted=False, target_node_iid=None
        ))

    task = Task(
        proc_id='fake', user_iid='test', input=[], output=outputs,
        budget=None, namespace=None
    )

    return Job(
        id='local-test', batch_id=None, task=task, retain=False,
        custodian=NodeInfo(
            identity=_dummy_keystore.identity, last_seen=get_timestamp_now(),
            dor_service='dummy', rti_service='dummy', p2p_address='in-memory',
            rest_address=None, retain_job_history=False,
            strict_deployment=False
        ),
        proc_name='proc-defg', t_submitted=0
    )


# ==============================================================================
# 3a. Local Tests — parametrized over all 16 input/output combinations
# ==============================================================================

# (d_val, e_val, include_f, include_g, expected_f, expected_g)
LOCAL_COMBOS = [
    # Both inputs, both outputs
    (5, 7, True, True, 10, 21),
    # One input, matching output
    (5, None, True, False, 10, None),
    (None, 7, False, True, None, 21),
    # No inputs, no outputs
    (None, None, False, False, None, None),
    # One input, both outputs declared
    (5, None, True, True, 10, None),
    (None, 7, True, True, None, 21),
    # Both inputs, one output declared
    (5, 7, True, False, 10, None),
    (5, 7, False, True, None, 21),
    # Input without matching output
    (5, None, False, True, None, None),
    (None, 7, True, False, None, None),
    # Outputs declared but no inputs
    (None, None, True, False, None, None),
    (None, None, False, True, None, None),
    (None, None, True, True, None, None),
    # Both inputs, no outputs declared
    (5, 7, False, False, None, None),
    (5, None, False, False, None, None),
    (None, 7, False, False, None, None),
]


@pytest.mark.integration
@pytest.mark.parametrize("d_val,e_val,include_f,include_g,expected_f,expected_g", LOCAL_COMBOS)
def test_processor_defg_local(
    dummy_namespace, d_val, e_val, include_f, include_g, expected_f, expected_g
):
    """Test DEFG processor local execution for all 16 I/O combinations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # write inputs that are provided
        if d_val is not None:
            write_value(os.path.join(temp_dir, 'd'), d_val)
        if e_val is not None:
            write_value(os.path.join(temp_dir, 'e'), e_val)

        # build minimal job with the right output declarations
        job = _make_local_job(include_f, include_g)

        # create and run processor
        status = JobStatus(
            state=JobStatus.State.INITIALISED, progress=0,
            output={}, notes={}, errors=[], message=None
        )
        proc_path = os.path.join(BASE_DIR, 'examples', 'simple', 'defg')
        proc = ProcessorDEFG(proc_path)
        proc.run(
            temp_dir, job,
            DummyProgressListener(temp_dir, status, dummy_namespace.dor),
            dummy_namespace, logging.getLogger('test.defg')
        )

        # check output f
        f_path = os.path.join(temp_dir, 'f')
        if expected_f is not None:
            assert os.path.isfile(f_path), "Expected output 'f' to be produced"
            with open(f_path, 'r') as f:
                assert json.load(f)['v'] == expected_f
        else:
            assert not os.path.isfile(f_path), "Output 'f' should not be produced"

        # check output g
        g_path = os.path.join(temp_dir, 'g')
        if expected_g is not None:
            assert os.path.isfile(g_path), "Expected output 'g' to be produced"
            with open(g_path, 'r') as f:
                assert json.load(f)['v'] == expected_g
        else:
            assert not os.path.isfile(g_path), "Output 'g' should not be produced"


# ==============================================================================
# 3b. Docker Integration Tests — key scenarios via RTI
# ==============================================================================

@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_docker_all_in_all_out(
    docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_defg_processor
):
    """Both inputs provided, both outputs declared → both produced."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    task = create_defg_task(proc_id, owner, d=5, e=7, include_f=True, include_g=True)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    status = wait_for_job_completion(rti_proxy, job.id, owner)
    assert_job_successful(status, expected_outputs=['f', 'g'])
    assert_data_object_content(
        dor_proxy, status.output['f'].obj_id, owner,
        expected={'v': 10}, temp_dir=test_context.testing_dir
    )
    assert_data_object_content(
        dor_proxy, status.output['g'].obj_id, owner,
        expected={'v': 21}, temp_dir=test_context.testing_dir
    )


@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_docker_d_only(
    docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_defg_processor
):
    """Only input d, only output f declared → f produced."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    task = create_defg_task(proc_id, owner, d=5, include_f=True, include_g=False)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    status = wait_for_job_completion(rti_proxy, job.id, owner)
    assert_job_successful(status, expected_outputs=['f'])
    assert_data_object_content(
        dor_proxy, status.output['f'].obj_id, owner,
        expected={'v': 10}, temp_dir=test_context.testing_dir
    )
    assert 'g' not in status.output


@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_docker_e_only(
    docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_defg_processor
):
    """Only input e, only output g declared → g produced."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    task = create_defg_task(proc_id, owner, e=7, include_f=False, include_g=True)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    status = wait_for_job_completion(rti_proxy, job.id, owner)
    assert_job_successful(status, expected_outputs=['g'])
    assert_data_object_content(
        dor_proxy, status.output['g'].obj_id, owner,
        expected={'v': 21}, temp_dir=test_context.testing_dir
    )
    assert 'f' not in status.output


@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_docker_empty(
    docker_available, session_node, rti_proxy, deployed_defg_processor
):
    """No inputs, no outputs → job succeeds with empty output."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    task = create_defg_task(proc_id, owner, include_f=False, include_g=False)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    status = wait_for_job_completion(rti_proxy, job.id, owner)
    assert status.state == JobStatus.State.SUCCESSFUL
    assert len(status.output) == 0


@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_docker_outputs_declared_no_inputs(
    docker_available, session_node, rti_proxy, deployed_defg_processor
):
    """Outputs declared but no inputs → job succeeds, no outputs produced."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    task = create_defg_task(proc_id, owner, include_f=True, include_g=True)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    status = wait_for_job_completion(rti_proxy, job.id, owner)
    assert status.state == JobStatus.State.SUCCESSFUL
    # optional outputs that weren't produced should not appear
    for name, obj in status.output.items():
        # if they appear, they should be None (not produced)
        assert obj is None, f"Output '{name}' should not have been produced"


@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_docker_inputs_but_partial_outputs(
    docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_defg_processor
):
    """Both inputs available but only f declared → only f produced."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    task = create_defg_task(proc_id, owner, d=5, e=7, include_f=True, include_g=False)
    result = rti_proxy.submit([task], with_authorisation_by=owner)
    job = result[0]

    status = wait_for_job_completion(rti_proxy, job.id, owner)
    assert_job_successful(status, expected_outputs=['f'])
    assert_data_object_content(
        dor_proxy, status.output['f'].obj_id, owner,
        expected={'v': 10}, temp_dir=test_context.testing_dir
    )
    assert 'g' not in status.output


# ==============================================================================
# 3c. Validation Tests — using ABC processor (no Docker containers needed)
# ==============================================================================

@pytest.mark.integration
@pytest.mark.docker_only
def test_validation_missing_required_output(
    docker_available, session_node, rti_proxy, deployed_abc_processor
):
    """Submit ABC task without required output 'c' → validation error."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    # Build a task with inputs but NO output 'c' (which is required for ABC)
    task = (TaskBuilder(proc_id, owner.identity.id)
            .with_input_value('a', {'v': 1})
            .with_input_value('b', {'v': 2})
            .with_budget(memory=1024)
            .build())

    with pytest.raises(RemoteError) as exc_info:
        rti_proxy.submit([task], with_authorisation_by=owner)

    error_str = str(exc_info.value._details)
    assert "missing required output" in error_str.lower()


@pytest.mark.integration
@pytest.mark.docker_only
def test_validation_unknown_output_name(
    docker_available, session_node, rti_proxy, deployed_abc_processor
):
    """Submit ABC task with unknown output 'z' → validation error."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    task = (TaskBuilder(proc_id, owner.identity.id)
            .with_input_value('a', {'v': 1})
            .with_input_value('b', {'v': 2})
            .with_output('c', owner.identity.id)
            .with_output('z', owner.identity.id)
            .with_budget(memory=1024)
            .build())

    with pytest.raises(RemoteError) as exc_info:
        rti_proxy.submit([task], with_authorisation_by=owner)

    error_str = str(exc_info.value._details)
    assert "unknown output" in error_str.lower()


@pytest.mark.integration
@pytest.mark.docker_only
def test_validation_unknown_input_name(
    docker_available, session_node, rti_proxy, deployed_abc_processor
):
    """Submit ABC task with unknown input 'z' → validation error."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_abc_processor.obj_id
    owner = session_node.keystore

    task = (TaskBuilder(proc_id, owner.identity.id)
            .with_input_value('a', {'v': 1})
            .with_input_value('b', {'v': 2})
            .with_input_value('z', {'v': 99})
            .with_output('c', owner.identity.id)
            .with_budget(memory=1024)
            .build())

    with pytest.raises(RemoteError) as exc_info:
        rti_proxy.submit([task], with_authorisation_by=owner)

    error_str = str(exc_info.value._details)
    assert "unknown input" in error_str.lower()


# ==============================================================================
# 3d. Provenance Chain Test
# ==============================================================================

@pytest.mark.integration
@pytest.mark.docker_only
def test_defg_provenance_chain(
    docker_available, test_context, session_node, dor_proxy, rti_proxy, deployed_defg_processor
):
    """Run 3 DEFG iterations chaining output f as input d, verify provenance."""
    if not docker_available:
        pytest.skip("Docker is not available")

    proc_id = deployed_defg_processor.obj_id
    owner = session_node.keystore

    # Create initial data object d0 = {"v": 1}
    d0_path = test_context.create_file_with_content(
        f"{generate_random_string(4)}.json", json.dumps({'v': 1})
    )
    d0 = dor_proxy.add_data_object(
        d0_path, owner.identity, False, False, 'JSONObject', 'json'
    )

    prev_obj = d0
    expected_val = 1
    produced_objects = []

    for i in range(3):
        expected_val *= 2  # each iteration doubles

        # Build task with by-reference input d → output f
        task = (TaskBuilder(proc_id, owner.identity.id)
                .with_input_reference('d', prev_obj.obj_id)
                .with_output('f', owner.identity.id,
                             target_node_iid=session_node.identity.id)
                .with_budget(memory=1024)
                .build())

        result = rti_proxy.submit([task], with_authorisation_by=owner)
        job = result[0]
        status = wait_for_job_completion(rti_proxy, job.id, owner)

        assert_job_successful(status, expected_outputs=['f'])
        assert_data_object_content(
            dor_proxy, status.output['f'].obj_id, owner,
            expected={'v': expected_val}, temp_dir=test_context.testing_dir
        )

        prev_obj = status.output['f']
        produced_objects.append(prev_obj)

    # Verify provenance of the final output (f3, value=8)
    provenance = dor_proxy.get_provenance(produced_objects[-1].c_hash)
    assert provenance is not None
    assert len(provenance.steps) >= 3
