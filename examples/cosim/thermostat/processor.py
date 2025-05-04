import json
import logging
import os
import socket
import time
import traceback
from typing import List, Dict, Tuple
from pydantic import BaseModel

from simaas.rti.schemas import Job, Severity, BatchStatus
from simaas.core.processor import ProcessorBase, ProgressListener, Namespace


class Parameters(BaseModel):
    threshold_low: float
    threshold_high: float


class Result(BaseModel):
    state: List[Tuple[float, int]]


class ThermostatProcessor(ProcessorBase):
    """
    ThermostatProcessor simulates a thermostat controller that interacts with a room model via socket communication.
    The processor reads parameters, connects to a room simulator, monitors the room's temperature, and adjusts the heater's state based on predefined temperature thresholds.

    Attributes:
        _is_cancelled (bool): Flag indicating whether the simulation process has been cancelled.
    """

    def __init__(self, proc_path: str) -> None:
        """
        Initializes the thermostat processor with the given processor path.

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
        Runs the thermostat simulation process. The processor connects to a room simulator, receives temperature data,
        adjusts the heater's state based on predefined thresholds, and writes the results to a file. The process can be
        cancelled, and progress updates and messages are sent via the listener.

        Args:
            wd_path (str): Path to the working directory where input parameters and output files are stored.
            job (Job): The job object representing the task being processed.
            listener (ProgressListener): Listener for reporting progress updates, messages, and output availability.
            namespace (Namespace): Namespace for accessing RTI, batch status, and other data objects.
            logger (logging.Logger): Logger for logging messages and errors during the processing.

        Notes:
            - The processor expects a `parameters` file in the `wd_path` containing the thermostat control parameters (e.g., temperature thresholds).
            - The simulation runs by receiving temperature data from a room simulator and sending control commands to adjust the heater (ON/OFF).
            - Progress updates and messages are reported via the `listener`.
            - Results are saved in the `result` file within the `wd_path`.
        """

        # Step 1: Read parameters from the 'parameters' file
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)
        listener.on_progress_update(10)

        # Step 2: Get batch status to find the address of the room simulator
        try:
            batch_status: BatchStatus = namespace.rti.get_batch_status(job.batch_id)
        except Exception as e:
            logger.error(f"Getting batch status from {namespace.custodian_address()}: {e}")
            raise e

        # Get room simulator address from batch status
        members: Dict[str, BatchStatus.Member] = {member.name: member for member in batch_status.members}
        room_info: BatchStatus.Member = members.get('room')
        address: str = room_info.ports['7001/tcp']
        host, port = address.split("://", 1)[-1].rsplit(":", 1)
        port: int = int(port)

        # Step 3: Connect to the room model simulator
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            time.sleep(5)  # Ensure Room Simulator starts first
            client.connect((host, port))
            listener.on_message(Severity.INFO, f"Thermostat Controller: Connected to Room Simulator")
            listener.on_progress_update(20)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            print(trace)
            raise e

        # Step 4: Start simulation loop to control heater based on temperature
        state: List[Tuple[float, int]] = []
        while not self._is_cancelled:
            try:
                # Receive step and temperature data
                data = client.recv(1024).decode().strip()
                if not data:
                    break

                if data == "TERMINATE":
                    break

                step, temp = data.split(",")
                step = int(step.split(':')[1])
                temp = float(temp.split(':')[1])
                listener.on_message(Severity.INFO, f"Thermostat Controller: Step {step}, Temp {temp:.2f}")

                # Determine heater state based on temperature thresholds
                if temp < p.threshold_low:
                    command = "HEATER_ON"
                    state.append((temp, 1))
                elif temp > p.threshold_high:
                    command = "HEATER_OFF"
                    state.append((temp, -1))
                else:
                    command = "NO_CHANGE"
                    state.append((temp, 0))

                # Send heater command to room simulator
                client.sendall(command.encode())

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(trace)
                raise e

        # Step 5: Close connection and finalize results
        client.close()
        listener.on_message(Severity.INFO, "Thermostat Controller: Simulation complete.")
        listener.on_progress_update(90)

        if self._is_cancelled:
            listener.on_message(Severity.INFO, "Thermostat Controller: Simulation cancelled.")
        else:
            result = {
                'state': state
            }

            # Write the result to file
            result_path = os.path.join(wd_path, "result")
            with open(result_path, 'w') as f:
                json.dump(result, f, indent=2)

            # Inform RTI that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(
                Severity.INFO, "Thermostat Controller: Simulation complete."
            )

    def interrupt(self) -> None:
        """
        Cancels the ongoing thermostat simulation process by setting the `_is_cancelled` flag to True.
        This stops the simulation at the next cancellation point and prevents further actions.

        This method can be called to stop the processor mid-execution if needed, particularly if the simulation
        is taking too long or if the user requests cancellation.
        """
        self._is_cancelled = True
