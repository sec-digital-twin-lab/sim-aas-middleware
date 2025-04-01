import json
import logging
import os
import socket
import traceback
from typing import List
from pydantic import BaseModel

from simaas.rti.schemas import Job, Severity
from simaas.core.processor import ProcessorBase, ProgressListener, Namespace


class Parameters(BaseModel):
    initial_temp: float
    heating_rate: float
    cooling_rate: float
    max_steps: int


class Result(BaseModel):
    temp: List[float]


class RoomProcessor(ProcessorBase):
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

        # wait for thermostat model to connect
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('0.0.0.0', 7001))
        server.listen(1)
        try:
            listener.on_message(Severity.INFO, f"Room Simulator: Waiting for connection...")
            conn, _ = server.accept()
            listener.on_message(Severity.INFO, f"Room Simulator: Connected to Thermostat Controller")
            listener.on_progress_update(20)
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            print(trace)
            raise e

        # start simulation loop
        step = 0
        temp: List[float] = [p.initial_temp]
        heater_on = False
        while step < p.max_steps and not self._is_cancelled:
            try:
                # Send current temperature and step number
                message = f"STEP:{step},TEMP:{temp[-1]}"
                conn.sendall(message.encode())
                print(f"Room Simulator: Sent -> {message}")

                # Receive command (Ensure it is received before proceeding)
                data = conn.recv(1024).decode().strip()  # Strip removes extra spaces/newlines
                if data == "NO_CHANGE":
                    print("Room Simulator: No command received, assuming no change.")
                elif data == "HEATER_ON":
                    heater_on = True
                elif data == "HEATER_OFF":
                    heater_on = False
                else:
                    break

                # Update temperature
                temp.append(round(temp[-1] + (p.heating_rate if heater_on else p.cooling_rate), 1))
                step += 1

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(trace)
                raise e

        # wrap up the co-simulation
        conn.sendall("TERMINATE".encode())
        conn.close()
        server.close()

        if self._is_cancelled:
            listener.on_message(Severity.INFO, f"Room Simulator: Simulation cancelled.")

        else:
            result = {
                'temp': temp
            }

            # write the result
            result_path = os.path.join(wd_path, "result")
            with open(result_path, 'w') as f:
                json.dump(result, f, indent=2)

            # inform the RTI that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(Severity.INFO, f"Room Simulator: Simulation complete.")

    def interrupt(self) -> None:
        self._is_cancelled = True
