import json
import logging
import os
import socket
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
    """
    RoomProcessor simulates the temperature changes in a room model based on heater control commands received
    from a thermostat controller. The processor communicates with the ThermostatController via socket connection,
    receiving commands to turn the heater on or off, and adjusting the room temperature accordingly.
    The simulation runs for a predefined number of steps or until cancellation is triggered.

    Attributes:
        _is_cancelled (bool): Flag indicating whether the simulation process has been cancelled.
    """

    def __init__(self, proc_path: str) -> None:
        """
        Initializes the room processor with the given processor path.

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
        Runs the room simulation process. The processor waits for a connection from the thermostat controller,
        then simulates temperature changes based on commands received from the controller (turning the heater on or off).
        It sends the current temperature to the thermostat controller, updates the room temperature, and writes the
        final temperature data to a result file. The process can be cancelled, and progress updates and messages
        are reported via the listener.

        Args:
            wd_path (str): Path to the working directory where input parameters and output files are stored.
            job (Job): The job object representing the task being processed.
            listener (ProgressListener): Listener for reporting progress updates, messages, and output availability.
            namespace (Namespace): Namespace for accessing RTI, batch status, and other data objects.
            logger (logging.Logger): Logger for logging messages and errors during the processing.

        Notes:
            - The processor expects a `parameters` file in the `wd_path` containing the room simulation parameters
              (e.g., initial temperature, heating/cooling rates, and maximum steps).
            - The simulation runs by receiving commands from the thermostat controller to turn the heater on or off.
              Temperature is updated based on these commands.
            - Progress updates and messages are reported via the `listener`.
            - Results are saved in the `result` file within the `wd_path`.
        """

        # Step 1: Read parameters from the 'parameters' file
        parameters_path: str = os.path.join(wd_path, 'parameters')
        with open(parameters_path, 'r') as f:
            content = json.load(f)
            p = Parameters.model_validate(content)
        listener.on_progress_update(10)

        # Step 2: Wait for connection from the thermostat controller
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('0.0.0.0', 7001))
        server.listen(1)
        server.settimeout(1.0)  # Set timeout to allow periodic cancellation checks
        try:
            listener.on_message(Severity.INFO, "Room Simulator: Waiting for connection...")
            conn = None
            while conn is None and not self._is_cancelled:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    # Timeout allows us to check _is_cancelled flag
                    continue

            if self._is_cancelled:
                server.close()
                listener.on_message(Severity.INFO, "Room Simulator: Cancelled while waiting for connection.")
                return

            listener.on_message(Severity.INFO, "Room Simulator: Connected to Thermostat Controller")
            listener.on_progress_update(20)
        except Exception as e:
            logger.error(f"Room Simulator: Connection failed: {e}")
            raise

        # Step 3: Start simulation loop to control room temperature based on received commands
        step = 0
        temp: List[float] = [p.initial_temp]
        heater_on = False
        while step < p.max_steps and not self._is_cancelled:
            try:
                # Send current temperature and step number to thermostat controller
                message = f"STEP:{step},TEMP:{temp[-1]}"
                conn.sendall(message.encode())
                logger.info(f"Room Simulator: Sent -> {message}")

                # Receive command from thermostat controller
                data = conn.recv(1024).decode().strip()
                if data == "NO_CHANGE":
                    logger.info("Room Simulator: No command received, assuming no change.")
                elif data == "HEATER_ON":
                    heater_on = True
                elif data == "HEATER_OFF":
                    heater_on = False
                else:
                    break

                # Update temperature based on whether heater is on or off
                temp.append(round(temp[-1] + (p.heating_rate if heater_on else p.cooling_rate), 1))
                step += 1

            except Exception as e:
                logger.error(f"Room Simulator: Simulation loop error: {e}")
                raise

        # Step 4: Wrap up the co-simulation
        conn.sendall("TERMINATE".encode())
        conn.close()
        server.close()

        # Step 5: Handle results and simulation status
        if self._is_cancelled:
            listener.on_message(Severity.INFO, "Room Simulator: Simulation cancelled.")
        else:
            result = {
                'temp': temp
            }

            # Write the result to file
            result_path = os.path.join(wd_path, "result")
            with open(result_path, 'w') as f:
                json.dump(result, f, indent=2)

            # Inform RTI that the result is available
            listener.on_output_available('result')
            listener.on_progress_update(100)
            listener.on_message(Severity.INFO, "Room Simulator: Simulation complete.")

    def interrupt(self) -> None:
        """
        Cancels the ongoing room simulation process by setting the `_is_cancelled` flag to True.
        This stops the simulation at the next cancellation point and prevents further actions.

        This method can be called to stop the processor mid-execution if needed, particularly if the simulation
        is taking too long or if the user requests cancellation.
        """
        self._is_cancelled = True
