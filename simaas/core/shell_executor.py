"""
Fork-safe subprocess execution for processor adapters.

Under Rosetta 2 (amd64-on-arm), any fork() from a process with active ZMQ I/O
threads deadlocks on glibc mutex state.  This module provides a workaround:

1. A background shell process is spawned BEFORE ZMQ initialisation.
2. The shell has no Python/ZMQ threads, so it can fork() safely.
3. Commands are sent to it via named pipes; results come back the same way.

Lifecycle (handled automatically by the JobRunner):
    shell_executor.start()   # before ZMQ
    ...                      # processor runs, calls shell_executor.run()
    shell_executor.stop()    # cleanup

Usage in processor code::

    from simaas.core.shell_executor import run
    result = run(['tar', 'xzf', 'archive.tar.gz'], cwd='/data', check=True)

The returned object is a standard ``subprocess.CompletedProcess``.
"""

import os
import shlex
import subprocess
import threading

_PIPE_CMD = '/tmp/exec_cmd'
_PIPE_RC = '/tmp/exec_rc'
_FILE_STDOUT = '/tmp/exec_stdout'
_FILE_STDERR = '/tmp/exec_stderr'

_lock = threading.Lock()
_executor_process: subprocess.Popen | None = None

_EXECUTOR_SCRIPT = """\
while true; do
    cmd=$(cat /tmp/exec_cmd)
    [ -z "$cmd" ] && continue
    eval "$cmd" > /tmp/exec_stdout 2> /tmp/exec_stderr
    echo $? > /tmp/exec_rc
done
"""


def start() -> None:
    """Start the background shell executor.

    Must be called BEFORE any ZMQ context or threading is initialised.
    The executor is a plain ``/bin/sh`` process spawned via fork+exec,
    which is safe because no ZMQ threads exist at this point.
    """
    global _executor_process

    if _executor_process is not None:
        return

    # Create named pipes (remove stale ones first)
    for path in (_PIPE_CMD, _PIPE_RC):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        os.mkfifo(path)

    # fork+exec into a fresh shell — safe because no ZMQ threads yet
    _executor_process = subprocess.Popen(
        ['sh', '-c', _EXECUTOR_SCRIPT],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop() -> None:
    """Stop the background shell executor and clean up pipes."""
    global _executor_process

    if _executor_process is None:
        return

    _executor_process.terminate()
    try:
        _executor_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _executor_process.kill()
        _executor_process.wait()
    _executor_process = None

    for path in (_PIPE_CMD, _PIPE_RC, _FILE_STDOUT, _FILE_STDERR):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def run(cmd, cwd=None, check=False, **_kwargs) -> subprocess.CompletedProcess:
    """Run a command via the background shell executor (fork-safe).

    Drop-in replacement for ``subprocess.run()`` for use in processor adapters.
    Arguments are shell-quoted automatically when *cmd* is a list.

    :param cmd: command as a list of strings or a single shell string.
    :param cwd: working directory for the command.
    :param check: if True, raise ``subprocess.CalledProcessError`` on non-zero exit.
    :returns: ``subprocess.CompletedProcess`` with captured stdout/stderr.
    """
    if _executor_process is None:
        raise RuntimeError(
            "Shell executor not started. "
            "This function can only be used inside a processor's run() method."
        )

    with _lock:
        shell_cmd = ' '.join(shlex.quote(c) for c in cmd) if isinstance(cmd, (list, tuple)) else cmd
        if cwd:
            shell_cmd = f"cd {shlex.quote(str(cwd))} && {shell_cmd}"

        with open(_PIPE_CMD, 'w') as f:
            f.write(shell_cmd)

        with open(_PIPE_RC) as f:
            rc = int(f.read().strip())
        with open(_FILE_STDOUT) as f:
            stdout = f.read()
        with open(_FILE_STDERR) as f:
            stderr = f.read()

        result = subprocess.CompletedProcess(cmd, rc, stdout, stderr)
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd, stdout, stderr)
        return result
