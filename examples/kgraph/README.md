# Knowledge Graph Example: Emissions Calculation with SPARQL Integration

This example demonstrates how a processor can compute derived values and **import the
results as RDF triples into a knowledge graph database** via SPARQL, orchestrated by the
Sim-aaS Middleware.

The example consists of one processor:

- **`ProcessorEmissions`** - a processor that takes two numerical inputs (`electricity_consumption`
and `emission_rate`), computes emissions, and imports the result into a SPARQL-compatible
knowledge graph database.

## Objective

Calculate CO2 emissions from electricity consumption and an emission rate, then represent
the result as RDF triples and import them into a knowledge graph database.

This setup demonstrates how to:
- Read multiple input data objects and compute a derived value.
- Use an RDF mapping template to assemble a knowledge graph from computed data.
- Connect to a SPARQL endpoint and import the generated graph.
- Use the `SPARQLWrapper` provided by the Sim-aaS Middleware.

## Processor

- **`ProcessorEmissions`**:
  - Accepts inputs: `electricity_consumption` (numeric value), `emission_rate` (numeric value),
  `kgdb_config` (SPARQL endpoint configuration).
  - Computes `calculated_emissions = electricity_consumption * emission_rate`.
  - Reads an RDF mapping template (`rdf_mapping_template.json`) bundled with the processor.
  - Assembles an RDF graph by substituting the computed value into the template.
  - Imports the graph into the configured SPARQL endpoint using `SPARQLWrapper`.
  - Writes the computed emissions value to an output file `calculated_emissions`.
  - Reports progress and messages to the listener at each stage.
  - Supports cancellation during the process.

## RDF Mapping Template

The processor uses a JSON-based mapping template (`rdf_mapping_template.json`) to define:
- **Namespaces**: RDF vocabulary prefixes (e.g., `CE2M`, `OUM`, `OM`, `XSD`).
- **Graph identifier**: Named graph URI for the generated triples.
- **Value placeholder**: A substitution marker (`%%EMISSIONS_VALUE%%`) replaced with the
computed emissions value at runtime.
- **Triples**: A list of subject-predicate-object statements that form the knowledge graph,
including both URI references and typed literals.

## Running the Example using Python
Test cases with working code can be found in [test_processor_kgraph.py](../../simaas/tests/test_processor_kgraph.py).

## Running the Example using the CLI
> This example assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md).

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

> This example requires a running SPARQL-compatible knowledge graph database. The database
> endpoint is configured via the `kgdb_config` input.

### Start a SPARQL Database
Start a Blazegraph instance using Docker:
```shell
docker run -d --name blazegraph -p 9999:8080 lyrasis/blazegraph:2.1.5
```

Verify that Blazegraph is ready:
```shell
curl -s "http://localhost:9999/bigdata/sparql?query=ASK+%7B+%3Fs+%3Fp+%3Fo+%7D"
```

This should return a valid SPARQL response. The SPARQL endpoint is available at
`http://localhost:9999/bigdata/sparql`. Note that the processor runs inside a Docker
container and cannot reach `localhost` on the host machine. Use the host's network IP
address (e.g., `192.168.x.x`) when configuring the `kgdb_config` input.

### Build the Processor Docker Image
Navigate to the `emissions` folder and run the build command:
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
The processor requires three inputs: `electricity_consumption`, `emission_rate`, and
`kgdb_config`. Example data for the first two can be found in `kgraph/data`. Add them
to the DOR:
```shell
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator electricity_consumption.json
simaas-cli dor --address <node_address> add --data-type JSONObject --data-format json --assume-creator emission_rate.json
```

The `kgdb_config` input will be provided by-value during job submission.

### SPARQL Endpoint Configuration (`kgdb_config`)

The `kgdb_config` input uses the `SPARQLWrapper.Config` schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `endpoint` | string | yes | SPARQL query endpoint URL |
| `update_endpoint` | string | no | SPARQL update endpoint URL (defaults to `endpoint`) |
| `username` | string | no | Username for HTTP basic authentication |
| `password` | string | no | Password for HTTP basic authentication |
| `default_graph` | string | no | Default named graph URI |
| `timeout` | integer | no | Request timeout in seconds (default: 30) |

Example using Blazegraph (replace `192.168.x.x` with the host's network IP):
```json
{
  "endpoint": "http://192.168.x.x:9999/bigdata/sparql",
  "update_endpoint": "http://192.168.x.x:9999/bigdata/sparql"
}
```

### Submit a Job
Submit a job for the `emissions` processor:
```shell
simaas-cli rti --address <node_address> job submit
```

The CLI will prompt you to select the processor (`proc-emissions`) and assign the two
numerical inputs by-reference. For the `kgdb_config` input, select `by-value` and enter
the SPARQL endpoint configuration as a JSON object (see above).

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
    "v": 41.2
}
```

The value `41.2` is the product of the example inputs: `100` (electricity consumption)
multiplied by `0.412` (emission rate). In addition to the output file, the processor has
imported the result as RDF triples into the configured SPARQL database.
