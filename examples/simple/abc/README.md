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
> This example assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md).

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

### Build the Processor Docker Image
Navigate to the `abc` folder and run the build command:
```shell
simaas-cli image build-local --arch linux/amd64 .
```

The `--arch linux/amd64` flag ensures compatibility with AWS Batch. It can be omitted
when running locally with a Docker RTI on the same platform. The command outputs the
path to the generated PDI file.

### Import the PDI to the DOR
The build command creates a PDI file locally. Import it to the node's DOR:
```shell
simaas-cli image import --address <node_address> <path_to_pdi_file>
```

### Deploy the Processor
Once the PDI has been imported, deploy it using the object id returned by the import
command:
```shell
simaas-cli rti --address <node_address> proc deploy --proc-id <pdi_object_id>
```

Verify the deployment:
```shell
simaas-cli rti --address <node_address> proc list
```

### Add Input Data to the DOR
The processor requires two inputs: `a` and `b` - both simple JSON objects. Example input
files are provided under `abc/data`. Add them to the DOR:
```shell
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator data_object_a.json
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator data_object_b.json
```

### Submit a Job
Submit a job using the two data objects as input:
```shell
simaas-cli rti --address <node_address> job submit
```

The CLI will prompt you to select the processor (`proc-abc`), assign the two input data
objects by-reference, and configure the output. Note the job id displayed at the end.

### Check Job Status and Retrieve Results
Check the status of the job using the job id:
```shell
simaas-cli rti --address <node_address> job status <job_id>
```

Once the job state is `successful`, download the output data object:
```shell
simaas-cli dor --address <node_address> download <obj_id>
```

The result should look like this:
```json
{
    "v": 3
}
```
