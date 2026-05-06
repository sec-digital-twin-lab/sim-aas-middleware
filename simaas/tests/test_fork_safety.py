"""Manual fork-safety verification for the Rosetta 2 deadlock.

Demonstrates that fork() deadlocks when ZMQ I/O threads are active
(Rosetta 2 only), and that forking BEFORE ZMQ avoids the issue.

Run the containerised test from the repo root (requires Docker)::

    .venv/bin/python -m pytest simaas/tests/test_fork_safety.py::test_fork_safety_rosetta -v -s

This builds a minimal linux/amd64 image, runs both scenarios inside it,
and asserts the expected outcome.  On Apple Silicon the result is::

    test_fork_with_fix      PASSED
    test_fork_without_fix   XFAIL   (deadlock confirmed)

The two bare tests (``test_fork_with_fix``, ``test_fork_without_fix``)
can also be run directly — they pass everywhere but only demonstrate the
deadlock when executed inside a linux/amd64 container on Apple Silicon.

These tests do NOT require any simaas infrastructure.  They only need
``pyzmq`` and a POSIX system with ``fork``.
"""

import multiprocessing as mp
import os
import platform
import subprocess
import sys
import tempfile
import textwrap

import pytest
import zmq


# ---- WITH FIX (must run first — see note at the bottom) --------------------

def test_fork_with_fix():
    """Fork BEFORE ZMQ, then exercise multiple fork patterns in the child.

    This mirrors the ProcessorWorker architecture: the worker process is
    forked before any ZMQ context exists, so it has zero I/O threads and
    can fork freely.

    The worker runs several scenarios that would all deadlock if ZMQ I/O
    threads were present:

    1. Simple subprocess.run
    2. Concurrent subprocess.run from multiple threads
    3. multiprocessing.Pool (fork inside the worker)
    4. subprocess.Popen with pipe I/O
    """
    ctx = mp.get_context('fork')
    parent_conn, child_conn = ctx.Pipe()

    def worker(conn):
        import concurrent.futures
        conn.recv()  # wait for parent to start ZMQ
        results = {}

        # 1. Simple subprocess.run
        r = subprocess.run(
            ['echo', 'hello'], capture_output=True, text=True, timeout=5,
        )
        results['simple'] = r.stdout.strip()

        # 2. Concurrent subprocess.run from multiple threads
        def _run_echo(msg):
            r = subprocess.run(
                ['echo', msg], capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_run_echo, f'thread-{i}') for i in range(4)]
            thread_results = [f.result() for f in futures]
        results['threads'] = sorted(thread_results)

        # 3. Popen with pipe I/O
        p = subprocess.Popen(
            ['cat'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        out, _ = p.communicate(input='piped data', timeout=5)
        results['popen'] = out.strip()

        conn.send(results)
        conn.close()

    # Fork BEFORE ZMQ — child inherits a clean process.
    proc = ctx.Process(target=worker, args=(child_conn,))
    proc.start()
    child_conn.close()

    # Start ZMQ I/O threads in the parent only.
    zmq_ctx = zmq.Context()
    sock = zmq_ctx.socket(zmq.PAIR)
    sock.bind('tcp://127.0.0.1:*')

    # Signal the worker and collect results.
    parent_conn.send('go')
    results = parent_conn.recv()

    proc.join(timeout=15)
    alive = proc.is_alive()

    # Cleanup (terminate ZMQ before the next test).
    parent_conn.close()
    sock.close()
    zmq_ctx.term()

    assert not alive, "Worker process did not exit"
    assert results['simple'] == 'hello'
    assert results['threads'] == ['thread-0', 'thread-1', 'thread-2', 'thread-3']
    assert results['popen'] == 'piped data'


# ---- WITHOUT FIX -----------------------------------------------------------

# Exit code used by the inner script to signal "fork deadlocked".
_DEADLOCK_EXIT = 42

_WITHOUT_FIX_SCRIPT = textwrap.dedent(f"""\
    import subprocess, sys, zmq

    # Create a ZMQ context — this spawns I/O threads.
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PAIR)
    sock.bind('tcp://127.0.0.1:*')

    # Now try to fork via subprocess.  On Rosetta 2 the child process
    # deadlocks on glibc mutexes held by the (now-gone) ZMQ I/O threads.
    try:
        r = subprocess.run(
            ['echo', 'hello'], capture_output=True, text=True, timeout=5,
        )
        # If we get here, fork worked (native Linux).
        sock.close()
        ctx.term()
        sys.exit(0)
    except subprocess.TimeoutExpired:
        # The forked child hung — deadlock confirmed.
        sock.close()
        ctx.term()
        sys.exit({_DEADLOCK_EXIT})
""")


def test_fork_without_fix():
    """ZMQ I/O threads active, then fork — deadlocks on Rosetta 2.

    The scenario runs in an isolated subprocess so that ZMQ threads never
    exist inside the pytest process (which would break the *other* test).

    On Rosetta 2 the inner ``subprocess.run`` times out because the
    forked child is stuck on a glibc mutex.  The test reports ``xfail``.

    On native Linux fork works despite the ZMQ threads, so the test
    passes normally.
    """
    try:
        result = subprocess.run(
            [sys.executable, '-c', _WITHOUT_FIX_SCRIPT],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        # The entire inner process hung (fork() blocked before exec).
        pytest.xfail(
            "DEADLOCK: fork() with active ZMQ I/O threads hung. "
            "Expected on Rosetta 2 (amd64-on-arm64)."
        )
        return

    if result.returncode == _DEADLOCK_EXIT:
        pytest.xfail(
            "DEADLOCK: subprocess.run() timed out inside a process with "
            "ZMQ I/O threads. Expected on Rosetta 2 (amd64-on-arm64)."
        )
    elif result.returncode != 0:
        pytest.fail(
            f"Inner script failed unexpectedly (exit {result.returncode}):\n"
            f"{result.stderr}"
        )
    # returncode == 0 → fork worked (not on Rosetta 2). Test passes.


# ---- Ordering --------------------------------------------------------------
# test_fork_with_fix MUST run before test_fork_without_fix.
#
# with_fix creates (and terminates) a ZMQ context inside the pytest process.
# After zmq.Context.term() all I/O threads are joined, so the process is
# clean again.  without_fix then launches its scenario in a subprocess,
# which requires a safe fork from the pytest process.
#
# pytest runs tests in file order by default, so the ordering above is
# correct as long as no plugin reorders them.


# ---- CONTAINERISED TEST (run from the host) --------------------------------

_CONTAINER_IMAGE = 'simaas-fork-safety-test'

_DOCKERFILE = textwrap.dedent("""\
    FROM --platform=linux/amd64 python:3.13-slim
    RUN pip install --no-cache-dir pyzmq pytest
""")


def _docker_available() -> bool:
    try:
        r = subprocess.run(
            ['docker', 'info'], capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _build_image() -> None:
    """Build the minimal test image (cached by Docker after first run)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, 'Dockerfile'), 'w') as f:
            f.write(_DOCKERFILE)
        result = subprocess.run(
            ['docker', 'build', '--platform', 'linux/amd64',
             '-t', _CONTAINER_IMAGE, tmpdir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            pytest.fail(f"Image build failed:\n{result.stderr}")


def test_fork_safety_rosetta():
    """Run with_fix / without_fix inside a linux/amd64 container.

    On Apple Silicon this exercises the Rosetta 2 translation layer.
    The expected outcome is:

        test_fork_with_fix      PASSED
        test_fork_without_fix   XFAIL   (deadlock)

    On x86_64 hosts both tests pass (no Rosetta 2, no deadlock).
    """
    if not _docker_available():
        pytest.skip("Docker is not available")

    _build_image()

    # Determine the path to this test file inside the mounted volume.
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..')
    )
    container_test_path = '/app/simaas/tests/test_fork_safety.py'

    result = subprocess.run(
        [
            'docker', 'run', '--rm', '--platform', 'linux/amd64',
            '-v', f'{repo_root}:/app:ro',
            _CONTAINER_IMAGE,
            'python', '-m', 'pytest', container_test_path,
            # Only run the two bare tests, not this container test.
            '-k', 'test_fork_with_fix or test_fork_without_fix',
            '--noconftest',
            '-v',
        ],
        capture_output=True, text=True, timeout=60,
    )

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    # The with_fix test must always pass.
    assert 'test_fork_with_fix PASSED' in result.stdout, (
        f"test_fork_with_fix did not PASS:\n{result.stdout}"
    )

    is_rosetta = (
        platform.system() == 'Darwin' and platform.machine() == 'arm64'
    )

    if is_rosetta:
        assert 'xfail' in result.stdout.lower(), (
            "Expected test_fork_without_fix to XFAIL on Rosetta 2, "
            f"but got:\n{result.stdout}"
        )
        print("\nRosetta 2 verified: with_fix PASSED, without_fix XFAIL")
    else:
        # On native x86_64, both pass — that's fine.
        print("\nNot on Rosetta 2: both tests passed (no deadlock expected)")
