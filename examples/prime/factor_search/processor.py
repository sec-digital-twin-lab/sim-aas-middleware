import json
import logging
import os
from typing import List, Set, Optional
from pydantic import BaseModel
from simaas.rti.schemas import Job

from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace


class Parameters(BaseModel):
    start: int
    end: int
    number: int


class Result(BaseModel):
    factors: List[int]


class ProcessorFactorSearch(ProcessorBase):
    """
    ProcessorFactorSearch is a processor that performs a factor search on a given number.
    It searches for all factors of a specified number within a given range and stores the results
    in a file. The process can be cancelled, and an optional limit can be set on the number of factors
    to consider.

    Attributes:
        _is_cancelled (bool): Flag indicating whether the factor search has been cancelled.
        _limit (Optional[int]): The maximum value up to which the factor search should run. If None, there is no limit.
    """

    def __init__(self, proc_path: str, limit: Optional[int] = None) -> None:
        """
        Initializes the processor with the given path and optional limit on the factor search.

        Args:
            proc_path (str): Path to the processor's working directory.
            limit (Optional[int], optional): Maximum value to consider during the factor search. Defaults to None (no limit).
        """
        super().__init__(proc_path)
        self._is_cancelled = False
        self._limit = limit

    def run(
            self,
            wd_path: str,
            job: Job,
            listener: ProgressListener,
            namespace: Namespace,
            logger: logging.Logger
    ) -> None:
        """
        Executes the factor search process by reading parameters, performing the factor search, and
        writing the results to a file. The search finds all factors of the specified number within a
        given range. The process can be cancelled mid-execution.

        Args:
            wd_path (str): Path to the working directory where input parameters and output files are stored.
            job (Job): The job object representing the task being processed.
            listener (ProgressListener): Listener for reporting progress updates, messages, and output availability.
            namespace (Namespace): Namespace for accessing RTI, keystore, and data objects.
            logger (logging.Logger): Logger for logging messages and errors during the processing.

        Notes:
            - The processor expects a `parameters` file in the `wd_path` containing the input parameters (`start`, `end`, and `number`).
            - The factor search iterates from `start` to `end`, adding factors of `number` to a set.
            - The process can be interrupted if the `_is_cancelled` flag is set or if a `limit` is reached.
            - The result is saved in the `result` file within the `wd_path`.
            - Progress and messages are reported using the provided listener.
        """

        # Step 1: Read parameters from the 'parameters' file
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)

        # Step 2: Perform factor search
        factors: Set[int] = set()
        start = p.start
        end = p.end
        for i in range(start, end):
            if p.number % i == 0:
                factors.add(i)
                factors.add(p.number // i)

            # Handle cancellation and limit checks
            if self._is_cancelled:
                print(f"Factor search was cancelled at i={i} (start={start} end={end})")
                break
            elif self._limit is not None and i >= self._limit:
                break

        # Step 3: If not cancelled, write the result to the 'result' file
        if not self._is_cancelled:
            result = Result(factors=sorted(list(factors)))
            result_path = os.path.join(wd_path, 'result')
            with open(result_path, 'w') as f:
                json.dump(result.model_dump(), f, indent=2)

            # Step 4: Inform the listener that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(Severity.INFO, f"We are done: {json.dumps(result.model_dump(), indent=2)}")

    def interrupt(self) -> None:
        """
        Cancels the ongoing factor search process by setting the `_is_cancelled` flag to True.
        This stops the search at the next cancellation point and prevents further factorisation.

        This method can be called to stop the processor mid-execution if needed, particularly
        if the search is taking too long or if the user requests cancellation.
        """
        self._is_cancelled = True
