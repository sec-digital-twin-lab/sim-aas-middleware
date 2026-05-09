import json
import logging
import os
from typing import Any

from simaas.rti.schemas import Job

from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace


def read_value(data_object_path: str) -> int:
    with open(data_object_path, 'r') as f:
        a = json.load(f)
        return int(a['v'])


def write_value(data_object_path: str, v: Any) -> None:
    with open(data_object_path, 'w') as f:
        # noinspection PyTypeChecker
        json.dump({
            'v': v
        }, f, indent=4, sort_keys=True)


class ProcessorDEFG(ProcessorBase):
    """
    ProcessorDEFG is a processor with all-optional inputs and outputs, designed
    to test optional I/O data object handling.

    Logic:
    - If input 'd' exists AND output 'f' is declared in task outputs → write f = {"v": d["v"] * 2}
    - If input 'e' exists AND output 'g' is declared in task outputs → write g = {"v": e["v"] * 3}
    """

    def __init__(self, proc_path: str) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(
            self,
            wd_path: str,
            job: Job,
            listener: ProgressListener,
            namespace: Namespace,
            logger: logging.Logger
    ) -> None:
        listener.on_progress_update(0)
        listener.on_message(Severity.INFO, "DEFG processor starting.")

        # determine which outputs are declared in the task
        declared_outputs = set()
        if job is not None and job.task is not None:
            declared_outputs = {o.name for o in job.task.output}

        # check which inputs exist
        d_path = os.path.join(wd_path, 'd')
        e_path = os.path.join(wd_path, 'e')
        has_d = os.path.isfile(d_path)
        has_e = os.path.isfile(e_path)

        if self._is_cancelled:
            return

        listener.on_progress_update(25)

        # process d → f
        if has_d and 'f' in declared_outputs:
            d_val = read_value(d_path)
            f_val = d_val * 2
            f_path = os.path.join(wd_path, 'f')
            write_value(f_path, f_val)
            listener.on_output_available('f')
            listener.on_message(Severity.INFO, f"f={f_val}")

        if self._is_cancelled:
            return

        listener.on_progress_update(50)

        # process e → g
        if has_e and 'g' in declared_outputs:
            e_val = read_value(e_path)
            g_val = e_val * 3
            g_path = os.path.join(wd_path, 'g')
            write_value(g_path, g_val)
            listener.on_output_available('g')
            listener.on_message(Severity.INFO, f"g={g_val}")

        if self._is_cancelled:
            return

        listener.on_progress_update(100)
        listener.on_message(Severity.INFO, "DEFG processor done.")

    def interrupt(self) -> None:
        self._is_cancelled = True
