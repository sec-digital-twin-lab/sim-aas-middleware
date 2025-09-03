import json
import logging
import os
import time
from typing import Any

from simaas.rti.schemas import Job

from simaas.core.helpers import get_timestamp_now
from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace, ProcessorRuntimeError


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
    """
    ProcessorABC is a simple processor that performs basic input/output operations.
    It reads two input values, `a` and `b`, from the working directory, computes their sum,
    and writes the result to an output file `c`. If an environment variable `SECRET_ABC_KEY`
    is defined, it uses that value instead of the sum of `a` and `b`. This is meant to
    demonstrate how secrets may be passed to a processor via environment variables.

    The processor also supports reporting progress and messages to a listener, allowing
    external monitoring of the processing status. It also supports cancellation of the
    operation.

    Attributes:
        _is_cancelled (bool): Flag indicating whether the processor has been cancelled.
    """

    def __init__(self, proc_path: str) -> None:
        """
        Initializes the processor with the given path.

        Args:
            proc_path (str): Path to the processor's working directory.
        """
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
        """
        Executes the processor, performing the following steps:
        1. Reads input values `a` and `b` from the working directory.
        2. Computes their sum, or uses the value from the `SECRET_ABC_KEY` environment variable.
        3. Writes the result to the output file `c` in the working directory.
        4. Reports progress and messages to the listener at various stages.
        5. Supports cancellation during the process.

        Args:
            wd_path (str): Path to the working directory where input and output data are stored.
            job (Job): The job object representing the task to be processed.
            listener (ProgressListener): Listener for reporting progress updates and messages.
            namespace (Namespace): Namespace for job-specific data and configurations.
            logger (logging.Logger): Logger for logging messages and errors during processing.

        Notes:
            - The processor expects input files `a` and `b` to be present in the `wd_path`.
            - The environment variable `SECRET_ABC_KEY` can override the sum of `a` and `b`.
            - The listener is used to report progress updates and availability of the output.
            - The processor will check for cancellation after each significant step.
        """

        def interruptable_sleep(seconds: float) -> None:
            """
            Sleeps for the specified duration. The sleep is interruptible if the duration is
            positive, allowing the processor to be cancelled during the sleep period.

            Args:
                seconds (float): Duration to sleep in seconds. Negative values result in
                                 uninterruptible sleep.
            """

            # does the absolute delay exceed the threshold?
            if abs(seconds) > 999:
                raise ProcessorRuntimeError(f"Sleep value exceeds threshold", {'seconds': seconds})

            if seconds >= 0:  # interruptible sleep
                t_done = get_timestamp_now() + seconds * 1000
                while get_timestamp_now() < t_done and not self._is_cancelled:
                    time.sleep(0.1)
            else:  # uninterruptible sleep
                time.sleep(abs(seconds))

        # Initial progress update
        listener.on_progress_update(0)
        listener.on_message(Severity.INFO, "This is a message at the very beginning of the process.")

        # Check for environment variable SECRET_ABC_KEY
        secret_abc_key: int = None
        if 'SECRET_ABC_KEY' in os.environ:
            secret_abc_key = int(os.environ['SECRET_ABC_KEY'])
            listener.on_message(Severity.INFO, f"Environment variable SECRET_ABC_KEY is defined: '{secret_abc_key}'.")
        else:
            listener.on_message(Severity.INFO, "Environment variable SECRET_ABC_KEY is not defined.")

        # Read input value 'a' from file
        a_path = os.path.join(wd_path, 'a')
        a = read_value(a_path)
        listener.on_progress_update(30)
        listener.on_message(Severity.INFO, f"a={a}")

        # Simulate processing time for 'a'
        interruptable_sleep(a)
        if self._is_cancelled:
            return

        # Read input value 'b' from file
        b_path = os.path.join(wd_path, 'b')
        b = read_value(b_path)
        listener.on_progress_update(60)
        listener.on_message(Severity.INFO, f"b={b}")

        # Simulate processing time for 'b'
        interruptable_sleep(b)
        if self._is_cancelled:
            return

        # Compute the result: use SECRET_ABC_KEY if available, otherwise sum 'a' and 'b'
        c = a + b if secret_abc_key is None else secret_abc_key

        # Write output value 'c' to file
        c_path = os.path.join(wd_path, 'c')
        write_value(c_path, c)
        listener.on_output_available('c')
        listener.on_progress_update(90)
        listener.on_message(Severity.INFO, f"c={c}")

        # Final progress update
        interruptable_sleep(0.2)
        if self._is_cancelled:
            return

        listener.on_progress_update(100)
        listener.on_message(Severity.INFO, "...and we are done!")

    def interrupt(self) -> None:
        """
        Sets the cancellation flag to True, allowing the processor to halt its execution
        at the next cancellation point.

        This method can be called to stop the processor mid-execution, particularly if
        the operation is taking too long or if the user needs to cancel the job.
        """
        self._is_cancelled = True
