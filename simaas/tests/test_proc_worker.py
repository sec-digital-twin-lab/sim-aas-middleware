"""Unit tests for the fork-isolated ProcessorWorker.

Tests the IPC mechanism (pipe + event) between the parent and the
fork-isolated child process using mock processors and namespaces.
No Docker, no ZMQ, no simaas node infrastructure required.
"""

import os
import threading
import time

import pytest

from simaas.core.keystore import Keystore
from simaas.core.proc_worker import ProcessorWorker
from simaas.core.processor import ProcessorBase, ProgressListener
from simaas.nodedb.schemas import NodeInfo
from simaas.rti.schemas import Job, Severity, Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROC_ABC_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'examples', 'simple', 'abc',
)


def _make_job(keystore: Keystore) -> Job:
    """Build a minimal but schema-valid Job for testing."""
    return Job(
        id='test-job-001',
        batch_id=None,
        proc_name='proc-abc',
        retain=False,
        t_submitted=0,
        custodian=NodeInfo(
            identity=keystore.identity,
            last_seen=0,
            dor_service='none',
            rti_service='none',
            p2p_address='tcp://127.0.0.1:4001',
            rest_address=None,
            retain_job_history=False,
            strict_deployment=False,
        ),
        task=Task(
            proc_id='test-proc',
            user_iid=keystore.identity.id,
            namespace=None,
            input=[],
            output=[],
        ),
    )


class _RecordingListener(ProgressListener):
    """Collects all listener events for later assertion."""

    def __init__(self):
        self.progress = []
        self.outputs = []
        self.messages = []

    def on_progress_update(self, progress: float) -> None:
        self.progress.append(progress)

    def on_output_available(self, output_name: str) -> None:
        self.outputs.append(output_name)

    def on_message(self, severity: Severity, message: str) -> None:
        self.messages.append((severity, message))


class _MockNamespaceDOR:
    def search(self, **kwargs):
        return [{'mock': True}]

    def statistics(self):
        return {'total': 42}


class _MockNamespaceRTI:
    def get_all_procs(self):
        return []


class _MockNamespace:
    def __init__(self, keystore):
        self._keystore = keystore
        self._dor = _MockNamespaceDOR()
        self._rti = _MockNamespaceRTI()

    def id(self):
        return 'ns-test'

    def name(self):
        return 'test-namespace'

    def custodian_address(self):
        return 'tcp://127.0.0.1:9999'

    def keystore(self):
        return self._keystore

    def destroy(self):
        pass

    @property
    def dor(self):
        return self._dor

    @property
    def rti(self):
        return self._rti


# --- Mock processors -------------------------------------------------------

class _SimpleProcessor(ProcessorBase):
    """Processor that exercises listener callbacks."""

    def run(self, wd_path, job, listener, namespace, logger):
        listener.on_progress_update(0)
        listener.on_message(Severity.INFO, 'started')
        listener.on_progress_update(50)
        listener.on_output_available('c')
        listener.on_progress_update(100)
        listener.on_message(Severity.INFO, 'done')

    def interrupt(self):
        pass


class _NamespaceCallingProcessor(ProcessorBase):
    """Processor that makes namespace DOR/RTI calls and writes results to files."""

    def run(self, wd_path, job, listener, namespace, logger):
        import json
        dor_result = namespace.dor.search()
        rti_result = namespace.rti.get_all_procs()
        ks_id = namespace.keystore().identity.id
        with open(os.path.join(wd_path, 'ns_results.json'), 'w') as f:
            json.dump({'dor': dor_result, 'rti': rti_result, 'ks_id': ks_id}, f)
        listener.on_progress_update(100)

    def interrupt(self):
        pass


class _EnvReadingProcessor(ProcessorBase):
    """Processor that reads an env var and writes it to a file."""

    def run(self, wd_path, job, listener, namespace, logger):
        value = os.environ.get('TEST_SECRET', 'NOT_FOUND')
        with open(os.path.join(wd_path, 'secret_value'), 'w') as f:
            f.write(value)
        listener.on_progress_update(100)

    def interrupt(self):
        pass


class _FailingProcessor(ProcessorBase):
    """Processor that raises an exception."""

    def run(self, wd_path, job, listener, namespace, logger):
        raise ValueError("intentional test failure")

    def interrupt(self):
        pass


class _InterruptableProcessor(ProcessorBase):
    """Processor that blocks until interrupted."""

    def __init__(self, proc_path):
        super().__init__(proc_path)
        self._interrupted = threading.Event()
        self.was_interrupted = False

    def run(self, wd_path, job, listener, namespace, logger):
        listener.on_progress_update(0)
        self._interrupted.wait(timeout=10)
        self.was_interrupted = self._interrupted.is_set()
        listener.on_progress_update(100)

    def interrupt(self):
        self._interrupted.set()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def keystore():
    return Keystore.new('test-worker')


@pytest.fixture(scope='module')
def job(keystore):
    return _make_job(keystore)


@pytest.fixture
def namespace(keystore):
    return _MockNamespace(keystore)


@pytest.fixture
def listener():
    return _RecordingListener()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_start_and_stop(self, keystore):
        proc = _SimpleProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        assert worker._process.is_alive()
        worker.stop()
        assert not worker._process.is_alive()

    def test_stop_without_start(self, keystore):
        proc = _SimpleProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.stop()  # should not raise


class TestListenerCallbacks:
    def test_all_callbacks_relayed(self, tmp_path, keystore, job, namespace, listener):
        proc = _SimpleProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            worker.run(str(tmp_path), job, listener, namespace)
        finally:
            worker.stop()

        assert listener.progress == [0, 50, 100]
        assert listener.outputs == ['c']
        assert listener.messages == [
            (Severity.INFO, 'started'),
            (Severity.INFO, 'done'),
        ]


class TestNamespaceProxy:
    def test_dor_rpc_round_trip(self, tmp_path, keystore, job, namespace, listener):
        import json
        proc = _NamespaceCallingProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            worker.run(str(tmp_path), job, listener, namespace)
        finally:
            worker.stop()

        with open(tmp_path / 'ns_results.json') as f:
            results = json.load(f)
        assert results['dor'] == [{'mock': True}]
        assert results['rti'] == []

    def test_keystore_available_directly(self, tmp_path, keystore, job, namespace, listener):
        import json
        proc = _NamespaceCallingProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            worker.run(str(tmp_path), job, listener, namespace)
        finally:
            worker.stop()

        with open(tmp_path / 'ns_results.json') as f:
            results = json.load(f)
        assert results['ks_id'] == keystore.identity.id


class TestEnvForwarding:
    def test_env_dict_applied_in_worker(self, tmp_path, keystore, job, namespace, listener):
        proc = _EnvReadingProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            worker.run(
                str(tmp_path), job, listener, namespace,
                env={'TEST_SECRET': 'hunter2'},
            )
        finally:
            worker.stop()

        secret_file = tmp_path / 'secret_value'
        assert secret_file.read_text() == 'hunter2'

    def test_env_does_not_leak_to_parent(self, tmp_path, keystore, job, namespace, listener):
        # Ensure the env var doesn't exist in the parent before the test.
        os.environ.pop('TEST_SECRET_LEAK', None)

        proc = _EnvReadingProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            worker.run(
                str(tmp_path), job, listener, namespace,
                env={'TEST_SECRET_LEAK': 'should_not_leak'},
            )
        finally:
            worker.stop()

        assert 'TEST_SECRET_LEAK' not in os.environ


class TestErrorPropagation:
    def test_exception_raised_in_parent(self, tmp_path, keystore, job, namespace, listener):
        proc = _FailingProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            with pytest.raises(ValueError, match="intentional test failure"):
                worker.run(str(tmp_path), job, listener, namespace)
        finally:
            worker.stop()

    def test_worker_trace_attached(self, tmp_path, keystore, job, namespace, listener):
        proc = _FailingProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        try:
            with pytest.raises(ValueError) as exc_info:
                worker.run(str(tmp_path), job, listener, namespace)
            assert hasattr(exc_info.value, '__worker_trace__')
            assert 'intentional test failure' in exc_info.value.__worker_trace__
        finally:
            worker.stop()

    def test_worker_death_gives_error(self, tmp_path, keystore, job, namespace, listener):
        proc = _SimpleProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()
        # Kill the worker before sending the run command.
        worker._process.kill()
        worker._process.join()
        with pytest.raises((RuntimeError, BrokenPipeError, EOFError)):
            worker.run(str(tmp_path), job, listener, namespace)
        worker.stop()


class TestInterrupt:
    def test_interrupt_reaches_processor(self, tmp_path, keystore, job, namespace, listener):
        proc = _InterruptableProcessor(_PROC_ABC_PATH)
        worker = ProcessorWorker(proc, keystore)
        worker.start()

        # Send interrupt from a background thread after a short delay.
        def _delayed_interrupt():
            time.sleep(0.3)
            worker.interrupt()

        t = threading.Thread(target=_delayed_interrupt)
        t.start()

        try:
            worker.run(str(tmp_path), job, listener, namespace)
        finally:
            t.join()
            worker.stop()

        # Verify the processor completed (interrupt was received).
        assert listener.progress == [0, 100]
