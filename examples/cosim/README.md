# Co-Simulation Example: Room and Thermostat Controllers

This example demonstrates a **synchronized co-simulation** between two processors -
a `Room` model and a `Thermostat` controller - coordinated through direct socket
communication, orchestrated by the Sim-aaS Middleware.

The example showcases how distributed models can interact in real-time to perform coupled
simulation tasks, relying on synchronized message exchange over a network connection.

## Objective

Simulate the thermal dynamics of a room under thermostat control:
- The **Room** model tracks temperature based on heating and cooling rates.
- The **Thermostat** model observes the room's temperature and decides whether to
turn the heater on or off based on configured thresholds.

This setup demonstrates how to:
- Perform **co-simulation** between coupled processors.
- Dynamically determine network addresses using the RTI.
- Exchange messages in real time via custom socket connections.
- Aggregate output from both sides of the interaction.

## Processors

### ThermostatProcessor
- Controls the heating system.
- Connects to the `RoomProcessor` via TCP.
- Based on temperature thresholds, sends commands:
  - `"HEATER_ON"` to raise temperature.
  - `"HEATER_OFF"` to stop heating.
  - `"NO_CHANGE"` to maintain current state.

### RoomProcessor
- Simulates room temperature evolution based on heater state.
- Listens for commands from the thermostat each timestep.
- Updates and returns a list of temperatures over time.

## Simulation Flow

1. The **RoomProcessor** starts a server socket on port `7001` and waits for a connection.
2. The **ThermostatProcessor**, once ready, retrieves the network address of the room job
from the RTI and connects to it.
3. Each simulation step:
   - The room sends its current temperature and timestep.
   - The thermostat reads the value and decides on the heater command.
   - The room applies the heating/cooling logic accordingly.
4. The loop continues for a predefined number of steps or until cancelled.

## Running the Example using Python
Test cases with working code can be found in [test_example_cosim.py](../../simaas/tests/test_example_cosim.py).

## Running the Example using the CLI
> This examples assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md).

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

### Build the Processor Docker Images
Navigate to the `room` and `thermostat` folders and run the build command for each:
```shell
simaas-cli image build-local --arch linux/amd64 .
```

The `--arch linux/amd64` flag ensures compatibility with AWS Batch. It can be omitted
when running locally with a Docker RTI on the same platform. The command outputs the
path to the generated PDI file.

### Import the PDIs to the DOR
The build command creates a PDI file locally for each processor. Import both to the
node's DOR:
```shell
simaas-cli image import --address <node_address> <path_to_room_pdi>
simaas-cli image import --address <node_address> <path_to_thermostat_pdi>
```

### Deploy the Processors
Once the PDIs have been imported, deploy both processors using the object ids returned
by the import commands:
```shell
simaas-cli rti --address <node_address> proc deploy --proc-id <room_pdi_object_id>
simaas-cli rti --address <node_address> proc deploy --proc-id <thermostat_pdi_object_id>
```

In order for this example to work, **both** processors need to be deployed. Verify the
deployment:
```shell
simaas-cli rti --address <node_address> proc list
```

### Add Input Data to the DOR
Both processors require parameters. Example data can be found in `cosim/data`. Add them
to the DOR:
```shell
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator room_parameters.json
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator thermostat_parameters.json
```

### Submit a Batch Job
This example requires a **batch job** for `room` and `thermostat`. Batch submission ensures
they are executed at the same time and are provided with discovery information at runtime
so they can connect to each other.
```shell
simaas-cli rti --address <node_address> job submit
```

When prompted, select the `proc-room` processor first and assign its parameters by-reference.
When asked "Add another task?", select **Yes** and add `proc-thermostat` with its parameters.
This creates a batch of two jobs.

### Check Job Status and Retrieve Results
The batch submission returns a job id for each task. Check the status of both jobs:
```shell
simaas-cli rti --address <node_address> job status <room_job_id>
simaas-cli rti --address <node_address> job status <thermostat_job_id>
```

Once both jobs are `successful`, download the output data objects:
```shell
simaas-cli dor --address <node_address> download <room_obj_id>
simaas-cli dor --address <node_address> download <thermostat_obj_id>
```

The room result contains the temperature history:
```json
{
  "temp": [15.0, 15.5, 16.0, 16.5]
}
```

The thermostat result contains the heater state at each step as `[temperature, state]` tuples:
```json
{
  "state": [[15.0, 1], [15.5, 1]]
}
```
