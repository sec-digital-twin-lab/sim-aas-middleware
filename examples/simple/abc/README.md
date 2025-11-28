# Simple Example: Basic Input/Output Processor

This example demonstrates how a simple processor can be used to process two input values 
and produce an output, primarily used for testing purposes.

The example consists of one processor:

- **`ProcessorABC`** - a processor that takes two inputs, `a` and `b`, and produces an 
output `c`.

## Objective

Compute the sum of two numbers `a` and `b`, or use a predefined value if the environment 
variable `SECRET_ABC_KEY` is set. The result is written to a file `c` in the working 
directory.

This setup demonstrates how to:
- Read input data from files.
- Optionally use an environment variable to influence computation.
- Write the output to a well-defined file.
- Report progress and job status via a listener.

## Processor

- **`ProcessorABC`**:
  - Accepts inputs: `a` (numeric value), `b` (numeric value).
  - Computes `c = a + b` unless the environment variable `SECRET_ABC_KEY` is defined.
  - Writes the result to a file `c` in the working directory.
  - Reports progress through the `listener` and sends messages at key steps.
  - Supports cancellation during computation.

## Running the Example using Python
Test cases with working code can be found in [test_example_abc.py](../../simaas/tests/test_example_abc.py).

## Running the Example using the CLI
> This examples assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md). 

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

The first step is to build the Processor Docker Image for the example. Use a terminal and
navigate to the `abc` folder, then use the CLI build command:
```shell
simaas-cli build-local .
```

You may want to specify the target platform explicitly (e.g., when planning to run it on 
a node using AWS RTI service):
```shell
simaas-cli build-local --arch linux/amd64 .
```

An example dialogue will look like this:
```
? Enter the target node's REST address 192.168.50.126:5001
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
Using processor path '/Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple/abc'.
Using GitHub credentials from env for user 'FooBar'.
Processor content hash: e09abdbc0a4c6d53a8b1066658f0557bed48916356e0cb94380182898fffdd8e
Building image 'foobar/proc-abc:e09abdbc0a4c6d53a8b1066658f0557bed48916356e0cb94380182898fffdd8e' for platform 'linux/amd64'. This may take a while...
Done building image 'foobar/proc-abc:e09abdbc0a4c6d53a8b1066658f0557bed48916356e0cb94380182898fffdd8e'.
Done exporting image to '/var/folders/p3/yjbdnj8n69d_dmzpw51dmtj40000gp/T/tmpfinq3k0n/image.tar'.
Done uploading image to DOR -> object id: edc2754a5baa19abf3647723404fb9d4edd76cee7c8fa7af881685c09dba0890
```

The command will ask for the target Sim-aaS node to which the image should be uploaded to. Once
the image has been uploaded, it should show up as a deployment option:
```shell
simaas-cli rti proc deploy
```

```
? Enter the node's REST address 192.168.50.126:5001
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
? Select the processor you would like to deploy: proc-abc <edc2...0890> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple:e09abd...
Deploying processor edc2...0890...Done
```

You can check the deployment status:
```shell
simaas-cli rti proc list
```

```
? Enter the node's REST address 192.168.50.126:5001
Found 1 processor(s) deployed at 192.168.50.126:5001:
- edc2...0890: proc-abc [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple@e09abd...
```

The example processor requires two inputs: `a` and `b` - both simple JSON objects. There
are example input files provided in the example folder under `abc/data`. In order to use 
them, they need to be added to the DOR. Navigate to the `data` folder and use the CLI command
to add the files to the DOR:
```shell
simaas-cli dor add --data-type JSONObject --data-format json data_object_a.json 
simaas-cli dor add --data-type JSONObject --data-format json data_object_b.json 
```

We can now use these two data objects as input for running a job with the `abc` example
processor that has been deployed earlier:
```shell
simaas-cli rti job submit   
```

The dialogue for job submission looks as follows:
```
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
? Enter the node's REST address 192.168.50.126:5001
? Select the processor for this task: edc2...0890: proc-abc [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple@e09abd...
Specify input interface item 'a' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'a': 1b6d...5d4e [JSONObject:json] name=data_object_a.json
Specify input interface item 'b' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'b': 8017...15ff [JSONObject:json] name=data_object_b.json
? Select the owner for the output data objects: ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw - test <test@test.com>
? Select the destination node for the output data objects: ztus...1bqw: test at 192.168.50.126:5001
? Should access to output data objects be restricted? No
? Give the task a name: test
? Select a budget for this task: 1 vCPUs, 2048 GB RAM memory
? Add another task? Note: multiple tasks submitted together will be executed as batch. No
Job submitted: 0AK3wEyF
```

Note the job id that is displayed at the end of the dialogue. Use the following to check the
status of the job:
```shell
simaas-cli rti job status 0AK3wEyF
```

The output should look like this:
```json
{
    "state": "successful",
    "progress": 100,
    "output": {
      "c": {
        "obj_id": "4ce5ff078eb624d46fc105b00b58e4f05f95f20e6453928900348328a6604308"
        // remaining information truncated
      }
    }
    // remaining information truncated
}
```

The output data object can be downloaded by using its object id (see above):
```shell
simaas-cli dor download 4ce5ff078eb624d46fc105b00b58e4f05f95f20e6453928900348328a6604308
```

The output should look like this:
```
? Enter the destination folder /Users/foobar/Desktop
? Enter the node's REST address 192.168.50.126:5001
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
Downloading 4ce5...4308 to /Users/foobar/Desktop/4ce5ff078eb624d46fc105b00b58e4f05f95f20e6453928900348328a6604308.json ...Done
```