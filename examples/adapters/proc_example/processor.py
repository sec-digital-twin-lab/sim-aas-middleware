import json
import logging
import os
import time
from typing import Any

from simaas.core.helpers import get_timestamp_now
from simaas.core.processor import ProcessorBase, ProgressListener, Severity


def read_value(data_object_path: str) -> int:
    with open(data_object_path, 'r') as f:
        a = json.load(f)
        return int(a['v'])


def write_value(data_object_path: str, v: Any) -> None:
    with open(data_object_path, 'w') as f:
        json.dump({
            'v': v
        }, f, indent=4, sort_keys=True)


class ExampleProcessor(ProcessorBase):
    def __init__(self, proc_path: str) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(self, wd_path: str, listener: ProgressListener, logger: logging.Logger) -> None:
        def interruptable_sleep(seconds: float) -> None:
            if seconds >= 0:  # interruptible
                t_done = get_timestamp_now() + seconds * 1000
                while get_timestamp_now() < t_done and not self._is_cancelled:
                    time.sleep(0.1)

            else:  # uninterruptible
                time.sleep(abs(seconds))

        listener.on_progress_update(0)
        listener.on_message(Severity.INFO, "This is a message at the very beginning of the process.")

        # read value from data object 'a'
        a_path = os.path.join(wd_path, 'a')
        a = read_value(a_path)
        listener.on_progress_update(30)
        listener.on_message(Severity.INFO, f"a={a}")

        interruptable_sleep(a)
        if self._is_cancelled:
            return

        # read value from data object 'b'
        b_path = os.path.join(wd_path, 'b')
        b = read_value(b_path)
        listener.on_progress_update(60)
        listener.on_message(Severity.INFO, f"b={b}")

        interruptable_sleep(b)
        if self._is_cancelled:
            return

        # write sum of a and b to data object 'c'
        c_path = os.path.join(wd_path, 'c')
        c = a + b
        write_value(c_path, c)
        listener.on_output_available('c')
        listener.on_progress_update(90)
        listener.on_message(Severity.INFO, f"c={c}")

        interruptable_sleep(0.2)
        if self._is_cancelled:
            return

        listener.on_progress_update(100)
        listener.on_message(Severity.INFO, "...and we are done!")

    def interrupt(self) -> None:
        self._is_cancelled = True
