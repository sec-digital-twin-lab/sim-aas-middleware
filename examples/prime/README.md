# Factorisation Example: Dynamic Child Job Submission

This example demonstrates how a processor can dynamically **create and manage child jobs on-the-fly** 
using the RTI (Runtime Interface) provided by the Sim-aaS Middleware.

The example consists of two processors:

- **`ProcessorFactorisation`** – an orchestrator that divides a factorisation task into sub-jobs.
- **`ProcessorFactorSearch`** – a worker that computes factors of a number over a specific sub-range.

## Objective

Factor a given number `N` by breaking the task into `K` sub-jobs. Each sub-job searches for factors 
in a subset of the range `[2, N)`.

This setup demonstrates how to:
- Dynamically discover available processors via the RTI.
- Submit child jobs at runtime.
- Monitor job status and retrieve output data.
- Aggregate results from distributed computation.

## Processors

- `ProcessorFactorisation`:
  - Accepts inputs: `N` (number to factor), `num_sub_jobs` (number of partitions).
  - Submits `num_sub_jobs` child jobs to `ProcessorFactorSearch`.
  - Waits for their completion, gathers the factor lists, and writes the final result.

- `ProcessorFactorSearch`:
  - Accepts a specific range (`start`, `end`) and the number `N`.
  - Computes and returns all factors of `N` in that range.

## Running the Example using Python
Test cases with working code can be found in [test_example_primes.py](../../simaas/tests/test_example_primes.py).

## Running the Example using the CLI
> This examples assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md). 

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

The first step is to build the Processor Docker Images for the example. Use a terminal and
navigate to the `factor_search` and `factorisation` folders, then use the CLI build command
for each:
```shell
simaas-cli build-local --arch linux/amd64 .
```

Note that in this example we build the processors for `linux/amd64` so they are compatible
with AWS Batch. They can also be run locally by a Docker RTI if needed.

If successful, the build output will look like this:
```
...
Building image 'foobar/proc-factor-search:c2625807211ba9f522963686584ba7f4cb20d946429f4f7a4d0f72f89ad26c6d' for platform 'linux/amd64'. This may take a while...
Done building image 'foobar/proc-factor-search:c2625807211ba9f522963686584ba7f4cb20d946429f4f7a4d0f72f89ad26c6d'.
Done exporting image to '/var/folders/p3/yjbdnj8n69d_dmzpw51dmtj40000gp/T/tmp2zvpel9z/image.tar'.
Done uploading image to DOR -> object id: 78a63f599948018f4649ab6f8a8a792a882d17d6aaa2fae91f594b547afec997
```
```
...
Building image 'foobar/proc-factorisation:41811988584f4c12fcf6b9d4cc557b3309f60db639adbca928f6e36d8366b6bf' for platform 'linux/amd64'. This may take a while...
Done building image 'foobar/proc-factorisation:41811988584f4c12fcf6b9d4cc557b3309f60db639adbca928f6e36d8366b6bf'.
Done exporting image to '/var/folders/p3/yjbdnj8n69d_dmzpw51dmtj40000gp/T/tmp1yg5jfet/image.tar'.
Done uploading image to DOR -> object id: 05b799d48485d13db740d73c2094cb24c3f1e0f653b77cd54701affdd26cf4e0
```

The command will ask for the target Sim-aaS node to which the images should be uploaded to. Once
the images have been uploaded, they should show up as a deployment option:
```shell
simaas-cli rti proc deploy
```
```
? Select the processor you would like to deploy: 
  proc-factor-search <78a6...c997> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/prime:c26258...
  proc-factorisation <05b7...f4e0> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/prime:418119...
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
- 78a6...c997: proc-factor-search [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/prime@c26258...
- 05b7...f4e0: proc-factorisation [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/prime@418119...```
```

For this example, we only need to manually submit a job for `factorisation`. This will
spawn a number of child jobs using `factor-search`. The `factorisation` processor requires
only one input `parameters`. An example file can be found in `prime/data`. In order to use 
it, it needs to be added to the DOR. Navigate to the `data` folder and use the CLI command
to add the files to the DOR:
```shell
simaas-cli dor add --data-type JSONObject --data-format json factorisation_parameters.json 
```

We can now use the data object as input for running a job with the `factorisation`
processor that has been deployed earlier:
```shell
simaas-cli rti job submit
```

The dialogue for job submission looks as follows:
```
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
? Enter the node's REST address 192.168.50.126:5001
? Select the processor for this task: 05b7...f4e0: proc-factorisation [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/prime@418119...
Specify input interface item 'parameters' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'parameters': 8142...ceb0 [JSONObject:json] name=factorisation_parameters.json
? Select the owner for the output data objects: ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw - test <test@test.com>
? Select the destination node for the output data objects: ztus...1bqw: test at 192.168.50.126:5001
? Should access to output data objects be restricted? No
? Give the task a name: test
? Select a budget for this task: 1 vCPUs, 2048 GB RAM memory
? Add another task? Note: multiple tasks submitted together will be executed as batch. No
Job submitted: C3baKxCE
```

Note the job id that is displayed at the end of the dialogue. Use the following to check the
status of the job:
```shell
simaas-cli rti job status C3baKxCE
```

The output should look like this:
```json
{
    "state": "successful",
    "progress": 100,
    "output": {
      "c": {
        "obj_id": "32466ed781a2783377c0f95dc5d04d6c6bc8ca92fd5aebe7f9383254c754e5cd"
        // remaining information truncated
      }
    }
    // remaining information truncated
}
```

The output data object can be downloaded by using its object id (see above):
```shell
simaas-cli dor download 32466ed781a2783377c0f95dc5d04d6c6bc8ca92fd5aebe7f9383254c754e5cd
```

The output should look like this:
```
Downloading 3246...e5cd to /Users/foobar/32466ed781a2783377c0f95dc5d04d6c6bc8ca92fd5aebe7f9383254c754e5cd.json ...Done
```

The content of the file can be viewed in the shell:
```shell
cat /Users/foobar/32466ed781a2783377c0f95dc5d04d6c6bc8ca92fd5aebe7f9383254c754e5cd.json 
```
```json
{
  "factors": [
    2,
    4,
    5,
    10,
    20,
    25,
    50
  ]
}
```