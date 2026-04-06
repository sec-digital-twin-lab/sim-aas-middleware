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
> This example assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md).

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

### Build the Processor Docker Images
Navigate to the `factor_search` and `factorisation` folders and run the build command
for each:
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
simaas-cli image import --address <node_address> <path_to_factor_search_pdi>
simaas-cli image import --address <node_address> <path_to_factorisation_pdi>
```

### Deploy the Processors
Once the PDIs have been imported, deploy both processors using the object ids returned
by the import commands:
```shell
simaas-cli rti --address <node_address> proc deploy --proc-id <factor_search_pdi_object_id>
simaas-cli rti --address <node_address> proc deploy --proc-id <factorisation_pdi_object_id>
```

In order for this example to work, **both** processors need to be deployed. Verify the
deployment:
```shell
simaas-cli rti --address <node_address> proc list
```

### Add Input Data to the DOR
Only the `factorisation` processor needs to be submitted manually - it will spawn child
jobs using `factor-search` at runtime. The `factorisation` processor requires one input
`parameters`. An example file can be found in `prime/data`. Add it to the DOR:
```shell
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator factorisation_parameters.json
```

### Submit a Job
Submit a job for the `factorisation` processor:
```shell
simaas-cli rti --address <node_address> job submit
```

The CLI will prompt you to select the processor (`proc-factorisation`), assign the
parameters data object by-reference, and configure the output. Note the job id displayed
at the end.

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
