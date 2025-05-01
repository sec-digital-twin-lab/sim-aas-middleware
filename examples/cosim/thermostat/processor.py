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
    def __init__(self, proc_path: str) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(
            self, wd_path: str, job: Job, listener: ProgressListener, namespace: Namespace, logger: logging.Logger
    ) -> None:
        # read the parameters
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)
        listener.on_progress_update(10)

        # get information about the batch
        try:
            batch_status: BatchStatus = namespace.rti.get_batch_status(job.batch_id)
        except Exception as e:
            logger.error(f"getting batch status from {namespace.custodian_address()}: {e}")
            raise e

        # obtain the address for the custom port (should be something like this 'tcp://host:port'
        members: Dict[str, BatchStatus.Member] = {member.name: member for member in batch_status.members}
        room_info: BatchStatus.Member = members.get('room')
        address: str = room_info.ports['7001/tcp']
        host, port = address.split("://", 1)[-1].rsplit(":", 1)
        port: int = int(port)

        # connect to room model
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

        # start simulation loop
        state: List[Tuple[float, int]] = []
        while not self._is_cancelled:
            try:
                # Receive step and temperature
                data = client.recv(1024).decode().strip()
                if not data:
                    break

                if data == "TERMINATE":
                    break

                step, temp = data.split(",")
                step = int(step.split(':')[1])
                temp = float(temp.split(':')[1])
                listener.on_message(Severity.INFO, f"Thermostat Controller: Step {step}, Temp {temp:.2f}")

                # Determine heater state
                if temp < p.threshold_low:
                    command = "HEATER_ON"
                    state.append((temp, 1))
                elif temp > p.threshold_high:
                    command = "HEATER_OFF"
                    state.append((temp, -1))
                else:
                    command = "NO_CHANGE"
                    state.append((temp, 0))

                client.sendall(command.encode())  # Send actual command or an empty string

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(trace)
                raise e

        client.close()
        listener.on_message(Severity.INFO, "Thermostat Controller: Simulation complete.")
        listener.on_progress_update(90)

        if self._is_cancelled:
            listener.on_message(Severity.INFO, f"Thermostat Controller: Simulation cancelled.")

        else:
            result = {
                'state': state
            }

            # write the result
            result_path = os.path.join(wd_path, "result")
            with open(result_path, 'w') as f:
                json.dump(result, f, indent=2)

            # inform the RTI that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(
                Severity.INFO, f"Thermostat Controller: Simulation complete."
            )

    def interrupt(self) -> None:
        self._is_cancelled = True
