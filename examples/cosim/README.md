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
> [here](../../docs/usage_build_processor_docker_image.md). 

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

The first step is to build the Processor Docker Images for the example. Use a terminal and
navigate to the `room` and `thermostat` folders, then use the CLI build command
for each:
```shell
simaas-cli build-local --arch linux/amd64 .
```

Note that in this example we build the processors for `linux/amd64` so they are compatible
with AWS Batch. They can also be run locally by a Docker RTI if needed.

If successful, the build output will look like this:
```
...
Building image 'foobar/proc-room:78da09dc6f956edbca8efca8541ca161959c37ca5cba911819baafc01ad465e6' for platform 'linux/amd64'. This may take a while...
Done building image 'foobar/proc-room:78da09dc6f956edbca8efca8541ca161959c37ca5cba911819baafc01ad465e6'.
Done exporting image to '/var/folders/p3/yjbdnj8n69d_dmzpw51dmtj40000gp/T/tmpat7lgcbl/image.tar'.
Done uploading image to DOR -> object id: 2198252b9a30cf0c50957bd02c3813d0c7f334f17cb4017a40ca1872faecab30
```
```
...
Building image 'foobar/proc-thermostat:e881f76ce18d93055401e55625cb4b205f4158b072fab072acc158b4cc864207' for platform 'linux/amd64'. This may take a while...
Done building image 'foobar/proc-thermostat:e881f76ce18d93055401e55625cb4b205f4158b072fab072acc158b4cc864207'.
Done exporting image to '/var/folders/p3/yjbdnj8n69d_dmzpw51dmtj40000gp/T/tmpszeldqiy/image.tar'.
Done uploading image to DOR -> object id: e8512063fc99be4b52936a1e2f83f34415a32dfa498abe418ab263e2e52c0e28
```

The command will ask for the target Sim-aaS node to which the images should be uploaded to. Once
the images have been uploaded, they should show up as a deployment option:
```shell
simaas-cli rti proc deploy
```
```
? Select the processor you would like to deploy: 
  proc-room <2198...ab30> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/cosim:78da09...
  proc-thermostat <e851...0e28> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/cosim:e881f7...
```
Note that in order for this example to work, **both** processors need to be deployed.

You can check the deployment status:
```shell
simaas-cli rti proc list
```

If deployment was successful, both processors should show up in the list.
```
? Enter the node's REST address 192.168.50.126:5001
Found 2 processor(s) deployed at 192.168.50.126:5001:
- 2198...ab30: proc-room [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/cosim@78da09...
- e851...0e28: proc-thermostat [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/cosim@e881f7...
```

For this example, we need to submit a **batch job** for `room` and `thermostat`. The reason
is that these two processor jointly perform a co-simulation. Batch submission ensures they
are executed at the same time and are provided with discovery information at runtime so they
can connect to each other. Both processors require parameters. Example data can be found in 
`cosim/data`. In order to use it, it needs to be added to the DOR. Navigate to the `data` 
folder and use the CLI command to add the files to the DOR:
```shell
simaas-cli dor add --data-type JSONObject --data-format json room_parameters.json 
simaas-cli dor add --data-type JSONObject --data-format json thermostat_parameters.json 
```

We can now use the data object as input for running a batch job with both, `room` and 
`thermostat`, processors that have been deployed earlier:  
```shell
simaas-cli rti job submit
```

The dialogue for job submission looks as follows:
```
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
? Enter the node's REST address 192.168.50.126:5001
? Select the processor for this task: 2198...ab30: proc-room [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/cosim@78da09...
Specify input interface item 'parameters' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'parameters': 3fb6...af95 [JSONObject:json] name=room_parameters.json
? Select the owner for the output data objects: ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw - test <test@test.com>
? Select the destination node for the output data objects: ztus...1bqw: test at 192.168.50.126:5001
? Should access to output data objects be restricted? No
? Give the task a name: room
? Select a budget for this task: 1 vCPUs, 2048 GB RAM memory
? Add another task? Note: multiple tasks submitted together will be executed as batch. Yes
? Select the processor for this task: e851...0e28: proc-thermostat [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/cosim@e881f7...
Specify input interface item 'parameters' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'parameters': 36a6...d80e [JSONObject:json] name=thermostat_parameters.json
? Select the owner for the output data objects: ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw - test <test@test.com>
? Select the destination node for the output data objects: ztus...1bqw: test at 192.168.50.126:5001
? Should access to output data objects be restricted? No
? Give the task a name: thermostat
? Select a budget for this task: 1 vCPUs, 2048 GB RAM memory
? Add another task? Note: multiple tasks submitted together will be executed as batch. No
Batch submitted: xjiu2cID
- Task 'room' executed by job vlgLyykd
- Task 'thermostat' executed by job GWzrZBzz
```

Note that multiple tasks have been submitted together to form a batch of jobs. The job id 
for each task is displayed at the end of the dialogue. Use the following to check the
status of the job:
```shell
simaas-cli rti job status vlgLyykd
simaas-cli rti job status GWzrZBzz
```

Both job status outputs should look like this:
```json
{
  "state": "successful",
  "progress": 100,
  "output": {
    "result": {
      "obj_id": "..."
      // remaining information truncated
    }
    // remaining information truncated
}
```

The result output data object can be downloaded by using their object id:
```shell
simaas-cli dor download 32466ed781a2783377c0f95dc5d04d6c6bc8ca92fd5aebe7f9383254c754e5cd  # for the room results
simaas-cli dor download c8f266a0eaf5338221acb8f241f5bca98d347f548e9b49d0d8303fed3f404e49  # for the thermostat results
```

The content of the file can be viewed in the shell:
```shell
cat /Users/foobar/32466ed781a2783377c0f95dc5d04d6c6bc8ca92fd5aebe7f9383254c754e5cd.json 
cat /Users/foobar/c8f266a0eaf5338221acb8f241f5bca98d347f548e9b49d0d8303fed3f404e49.json 
```

FThe room results should look like this:
```json
{
  "temp": [
    15.0,
    15.5,
    16.0,
    16.5
    // truncated
  ]
}
```

The thermostat results should look like this:
```json
{
  "state": [
    [
      15.0,
      1
    ],
    [
      15.5,
      1
    ]
    // truncated
  ]
}
```
