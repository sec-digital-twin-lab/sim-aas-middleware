import json
import logging
import os
import time
from typing import Any

from simaas.core.helpers import get_timestamp_now
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


class ProcessorABC(ProcessorBase):
    def __init__(self, proc_path: str) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(self, wd_path: str, listener: ProgressListener, namespace: Namespace, logger: logging.Logger) -> None:
        """
        Put all your code here that executes the model in this function.

        You can assume all input data are available in the working directory: `wd_path`. So for example,
        if you need a network which is stored in a file called 'network.json', you can expect it to be
        accessible at `os.path.join(wd_path, 'network.json')`. Likewise, if you produce output, it should
        be placed in the working directory using a well-defined name. For example, the output name is
        'result.json', then it should be stored at `os.path.join(wd_path, 'result.json')`

        When the processor is being executed, the Simulation-as-a-Service node that started the job will
        be monitoring the job. To publish the progress (in %), use `listener.on_progress_update(55)`. To
        publish a message, use `listener.on_message(Severity.INFO, "Hello...")`. Publishing progress and
        messages are not strictly required but help to communicate what's going.

        It is required to publish the availability of output. Use `listener.on_output_available('c')`,
        where 'c' in this example is the name of the output. Replace with the well-defined name of your
        output as needed.

        Below is an example processor implementation which doesn't do much but illustrates how a
        functional processor is implemented.
        """

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
