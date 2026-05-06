"""
Fork-isolated worker process for running processor adapters.

Spawns a child process BEFORE ZMQ initialisation so that the processor
runs in an environment free of ZMQ I/O threads.  This eliminates the
Rosetta 2 fork-deadlock: adapters can use subprocess.run(), matplotlib,
or any library that forks internally — no workarounds required.

Lifecycle (managed by JobRunner)::

    worker = ProcessorWorker(proc, keystore)
    worker.start()                # fork — call before ZMQ
    ...                           # parent does ZMQ/P2P setup
    worker.run(wd_path, job, ...) # blocks until processor finishes
    worker.stop()                 # cleanup
"""

import logging
import multiprocessing as mp
import os
import threading
import traceback as _tb

from simaas.core.keystore import Keystore
from simaas.core.processor import ProcessorBase, ProgressListener
from simaas.rti.schemas import Severity


# ---------------------------------------------------------------------------
# Worker-side helpers (execute inside the child process only)
# ---------------------------------------------------------------------------

class _PipeSender:
    """Thread-safe send/request wrapper around a multiprocessing Connection."""

    def __init__(self, conn):
        self._conn = conn
        self._lock = threading.Lock()

    def send(self, msg):
        with self._lock:
            self._conn.send(msg)

    def request(self, msg):
        """Send *msg* and block until the response arrives (namespace RPC)."""
        with self._lock:
            self._conn.send(msg)
            return self._conn.recv()

    def recv(self):
        return self._conn.recv()

    def close(self):
        self._conn.close()


class _ListenerProxy(ProgressListener):
    """ProgressListener that forwards callbacks to the parent over a pipe."""

    def __init__(self, pipe: _PipeSender):
        self._pipe = pipe

    def on_progress_update(self, progress: float) -> None:
        self._pipe.send(('progress', progress))

    def on_output_available(self, output_name: str) -> None:
        self._pipe.send(('output', output_name))

    def on_message(self, severity: Severity, message: str) -> None:
        self._pipe.send(('message', severity, message))


class _InterfaceProxy:
    """Generic proxy for a namespace sub-interface (DOR or RTI).

    Uses ``__getattr__`` to forward any method call to the parent via
    pipe-based RPC.  New methods added to the real interface are
    automatically supported without changes here.
    """

    def __init__(self, pipe: _PipeSender, interface_name: str):
        self._pipe = pipe
        self._interface = interface_name

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def _rpc(*args, **kwargs):
            resp = self._pipe.request(
                ('ns_req', self._interface, name, args, kwargs)
            )
            tag = resp[0]
            if tag == 'ns_ok':
                return resp[1]
            if tag == 'ns_err':
                raise resp[1]
            raise RuntimeError(f"Unexpected namespace response tag: {tag}")

        return _rpc


class _NamespaceProxy:
    """Proxy for SyncNamespace in the worker process.

    Simple properties (id, name, custodian_address, keystore) are served
    from values inherited via fork or sent in the run command.
    DOR and RTI method calls are forwarded to the parent.
    """

    def __init__(self, pipe, ns_id, ns_name, ns_custodian_address, keystore):
        self._id = ns_id
        self._name = ns_name
        self._custodian_address_val = ns_custodian_address
        self._keystore_val = keystore
        self._dor = _InterfaceProxy(pipe, 'dor')
        self._rti = _InterfaceProxy(pipe, 'rti')

    def id(self):
        return self._id

    def name(self):
        return self._name

    def custodian_address(self):
        return self._custodian_address_val

    def keystore(self):
        return self._keystore_val

    def destroy(self):
        pass

    @property
    def dor(self):
        return self._dor

    @property
    def rti(self):
        return self._rti


# ---------------------------------------------------------------------------
# Worker entry point
# ---------------------------------------------------------------------------

def _worker_main(conn, parent_conn, proc, keystore, interrupt_event):
    """Entry point for the fork-isolated worker process.

    This function runs in the child process.  It has NO ZMQ context or
    I/O threads — fork/subprocess is safe.
    """
    # Close the parent's pipe end (inherited via fork, not needed here).
    parent_conn.close()

    pipe = _PipeSender(conn)
    try:
        # Block until the parent sends the run command.
        msg = pipe.recv()
        if msg[0] != 'run':
            raise RuntimeError(f"Expected 'run' command, got '{msg[0]}'")
        config = msg[1]

        wd_path = config['wd_path']
        log_level = config.get('log_level', 'info')
        ns_cfg = config['namespace_config']

        # Apply environment variables (secrets injected after the fork).
        for key, value in config.get('env', {}).items():
            os.environ[key] = value

        # Reconstruct the Job inside the worker (avoids pickling issues).
        from simaas.rti.schemas import Job
        job = Job.model_validate(config['job'])

        # Set up a plain Python logger.  We avoid simaas.core.logging here
        # because its module-level Lock can be in a bad state after fork.
        log_level_map = {
            'debug': logging.DEBUG, 'info': logging.INFO,
            'warning': logging.WARNING, 'error': logging.ERROR,
        }
        logger = logging.getLogger('simaas.worker')
        logger.setLevel(log_level_map.get(log_level, logging.INFO))
        _fh = logging.FileHandler(os.path.join(wd_path, 'job.log'))
        _fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] [worker] %(message)s',
        ))
        logger.addHandler(_fh)

        # Background thread that translates the interrupt Event into a
        # proc.interrupt() call.
        def _interrupt_monitor():
            interrupt_event.wait()
            logger.info("Interrupt signal received — calling proc.interrupt()")
            try:
                proc.interrupt()
            except Exception as exc:
                logger.error(f"proc.interrupt() failed: {exc}")

        monitor = threading.Thread(target=_interrupt_monitor, daemon=True)
        monitor.start()

        # Build the proxy objects the processor will use.
        listener = _ListenerProxy(pipe)
        namespace = _NamespaceProxy(
            pipe, ns_cfg['id'], ns_cfg['name'],
            ns_cfg['custodian_address'], keystore,
        )

        # --- Run the processor (this is the whole point) ---
        proc.run(wd_path, job, listener, namespace, logger)

        pipe.send(('done',))

    except Exception as exc:
        trace = ''.join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        try:
            pipe.send(('error', exc, trace))
        except Exception:
            # Exception itself isn't picklable — fall back to RuntimeError.
            pipe.send(('error', RuntimeError(f"{type(exc).__name__}: {exc}"), trace))
    finally:
        pipe.close()


# ---------------------------------------------------------------------------
# Parent-side controller
# ---------------------------------------------------------------------------

class ProcessorWorker:
    """Manages a fork-isolated child process that runs a processor adapter.

    Usage::

        worker = ProcessorWorker(proc, keystore)
        worker.start()          # fork — before ZMQ
        ...                     # ZMQ / P2P setup
        worker.run(...)         # blocks until processor finishes
        worker.stop()           # cleanup
    """

    def __init__(self, proc: ProcessorBase, keystore: Keystore):
        self._proc = proc
        self._keystore = keystore
        self._parent_conn = None
        self._process = None
        self._interrupt_event = None

    def start(self):
        """Fork the worker process.  **Must** be called before any ZMQ context."""
        ctx = mp.get_context('fork')
        self._interrupt_event = ctx.Event()
        parent_conn, child_conn = ctx.Pipe()
        self._parent_conn = parent_conn
        self._process = ctx.Process(
            target=_worker_main,
            args=(child_conn, parent_conn, self._proc, self._keystore,
                  self._interrupt_event),
        )
        self._process.start()
        child_conn.close()  # parent doesn't need the child's end

    def interrupt(self):
        """Signal the worker to call ``proc.interrupt()``."""
        if self._interrupt_event:
            self._interrupt_event.set()

    def run(self, wd_path, job, listener, namespace, log_level='info', env=None):
        """Send the run command and relay events until the processor finishes.

        This **blocks** the calling thread.  While blocked it:

        * forwards ProgressListener events to *listener*
        * executes namespace RPC calls on *namespace*

        :param env: extra environment variables to inject into the worker
            (e.g. secrets that arrived after the fork via P2P handshake).

        Raises whatever exception the processor raised (if any).
        """
        # Send the run command with serialisable config.
        self._parent_conn.send(('run', {
            'wd_path': wd_path,
            'job': job.model_dump(),
            'log_level': log_level,
            'env': env or {},
            'namespace_config': {
                'id': namespace.id(),
                'name': namespace.name(),
                'custodian_address': str(namespace.custodian_address()),
            },
        }))

        # Event loop — relay messages until 'done' or 'error'.
        while True:
            try:
                msg = self._parent_conn.recv()
            except EOFError:
                ec = self._process.exitcode if self._process else '?'
                raise RuntimeError(
                    f"Worker process died unexpectedly (exit code: {ec})"
                )

            tag = msg[0]

            if tag == 'progress':
                listener.on_progress_update(msg[1])

            elif tag == 'output':
                listener.on_output_available(msg[1])

            elif tag == 'message':
                listener.on_message(msg[1], msg[2])

            elif tag == 'ns_req':
                self._handle_ns_request(msg, namespace)

            elif tag == 'done':
                break

            elif tag == 'error':
                exc, trace = msg[1], msg[2]
                exc.__worker_trace__ = trace
                raise exc

    def stop(self):
        """Terminate the worker process and close the pipe."""
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join()
        elif self._process:
            self._process.join(timeout=1)
        if self._parent_conn:
            self._parent_conn.close()
            self._parent_conn = None

    # -- internal ----------------------------------------------------------

    def _handle_ns_request(self, msg, namespace):
        _, interface, method, args, kwargs = msg
        try:
            target = namespace.dor if interface == 'dor' else namespace.rti
            result = getattr(target, method)(*args, **kwargs)
            self._parent_conn.send(('ns_ok', result))
        except Exception as exc:
            try:
                self._parent_conn.send(('ns_err', exc))
            except Exception:
                self._parent_conn.send(
                    ('ns_err', RuntimeError(f"{type(exc).__name__}: {exc}"))
                )
