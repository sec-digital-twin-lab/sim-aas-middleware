import json
import logging
import os
import subprocess
from typing import Dict, List

from simaas.rti.schemas import Job

from simaas.core.processor import ProcessorBase, ProgressListener, Namespace


def ping_address(address: str, count=4) -> str:
    result = subprocess.run(
        ["ping", "-c", str(count), address],
        capture_output=True,
        text=True
    )
    return result.stdout


def traceroute_address(address: str, max_hops=30) -> str:
    result = subprocess.run(
        ["traceroute", "-m", str(max_hops), address],
        capture_output=True,
        text=True
    )
    return result.stdout


class ProcessorPing(ProcessorBase):
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
        # Initial progress update
        listener.on_progress_update(0)

        # Read the parameters file
        with open(os.path.join(wd_path, 'parameters'), 'r') as f:
            parameters = json.load(f)
        print(f"using parameters: {parameters}")

        # Get the address
        address: str = parameters['address']

        result: Dict[str, List[str]] = {}
        if parameters['do_ping']:
            # Do the ping
            stdout: str = ping_address(address)
            print(f"ping stdout: {stdout}")
            result['ping_stdout'] = stdout.split('\n')

        if parameters['do_traceroute']:
            stdout: str = traceroute_address(address, max_hops=5)
            print(f"traceroute stdout: {stdout}")
            result['traceroute_stdout'] = stdout.split('\n')

        # Write result
        with open(os.path.join(wd_path, 'result'), 'w') as f:
            json.dump(result, f, indent=2)

        listener.on_output_available('result')
        listener.on_progress_update(100)

    def interrupt(self) -> None:
        self._is_cancelled = True
