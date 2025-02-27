import json
import logging
import os
from typing import List, Set, Optional

from pydantic import BaseModel

from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace


class Parameters(BaseModel):
    start: int
    end: int
    number: int


class Result(BaseModel):
    factors: List[int]


class ProcessorFactorSearch(ProcessorBase):
    def __init__(self, proc_path: str, limit: Optional[int] = None) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False
        self._limit = limit

    def run(self, wd_path: str, listener: ProgressListener, namespace: Namespace, logger: logging.Logger) -> None:
        # read the parameters
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)

        # run the search
        factors: Set[int] = set()
        start = p.start
        end = p.end
        for i in range(start, end):
            if p.number % i == 0:
                factors.add(i)
                factors.add(p.number // i)

            if self._is_cancelled:
                print(f"factor search was cancelled at i={i} (start={start} end={end})")
                break
            elif self._limit is not None and i >= self._limit:
                break

        if not self._is_cancelled:
            # create the result and write to file
            result = Result(factors=sorted(list(factors)))
            result_path = os.path.join(wd_path, 'result')
            with open(result_path, 'w') as f:
                json.dump(result.model_dump(), f, indent=2)

            # inform the RTI that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(Severity.INFO, f"we are done: {json.dumps(result.model_dump(), indent=2)}")


    def interrupt(self) -> None:
        self._is_cancelled = True
