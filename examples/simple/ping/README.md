# Ping Example: Processor Testing Outbound Connectivity

This example demonstrates a connectivity testing processor that tests various types of
network connectivity to an external host. The processor can perform ping, traceroute,
TCP connection tests, and UDP connection tests.

The example consists of:

- **`ProcessorPing`** - a processor that takes one input, `parameters`, and produces an output `result`
- **`test_server.py`** - combined TCP/UDP test server for connectivity testing

## Objective

Test outbound network connectivity from a running processor container to an external host
using multiple protocols.

This setup demonstrates how to:
- Execute system commands (`ping`, `traceroute`) from within a processor.
- Test TCP and UDP connectivity to specified ports.
- Conditionally run tests based on input parameters.
- Aggregate results from multiple connectivity tests into a single output.

## Connectivity Tests Supported

- **Ping Test** - ICMP ping to test basic network reachability
- **Traceroute Test** - Trace network path to destination
- **TCP Connection Test** - Attempt to establish TCP connection to specified port
- **UDP Connection Test** - Send UDP packet and optionally wait for response

## Parameters

The processor accepts these parameters:

```json
{
  "address": "target_host_or_ip",
  "do_ping": true,
  "do_traceroute": true,
  "do_tcp_test": true,
  "tcp_port": 8080,
  "tcp_timeout": 5,
  "do_udp_test": true,
  "udp_port": 8081,
  "udp_timeout": 5
}
```

## Test Server

For testing TCP/UDP connectivity, run the test server on the target machine:

```bash
python3 server.py --host 0.0.0.0 --tcp-port 8080 --udp-port 8081
```

## Running the Example using Python
Test cases with working code can be found in [test_processor_ping.py](../../simaas/tests/test_processor_ping.py).

## Running the Example using the CLI
> This examples assumes you have a Sim-aaS Node instance running, read the documentation
> [here](../../docs/usage_run_simaas_node.md) to learn how to do that.

> If you have not already done so, read the documentation on the build command
> [here](../../docs/usage_manage_processor_docker_images.md).

> Processor Docker Images depend on the sim-aas-middleware repository. At the time of writing,
> this repository is private. Ensure the following environment variables are set with access
> to this repository: `GITHUB_USERNAME` and `GITHUB_TOKEN`.

### Build the Processor Docker Image
Navigate to the `ping` folder and run the build command:
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

### Submit a Job
The processor takes a single `parameters` input. Submit a job:
```shell
simaas-cli rti --address <node_address> job submit
```

The CLI will prompt you to select the processor (`proc-ping`) and specify the input.
When prompted for the `parameters` input, select `by-value` and enter a JSON object like:
```json
{"address": "192.168.50.113", "do_ping": true, "do_traceroute": true, "do_tcp_test": true, "tcp_port": 8080, "tcp_timeout": 5, "do_udp_test": true, "udp_port": 8081, "udp_timeout": 5}
```

Adjust the `address` field to match your target host.

### Check Job Status and Retrieve Results
Check the status of the job using the job id:
```shell
simaas-cli rti --address <node_address> job status <job_id>
```

Once the job state is `successful`, download the output data object:
```shell
simaas-cli dor --address <node_address> download <obj_id>
```

The result contains the stdout of each test that was enabled:
```json
{
  "ping_stdout": [
    "PING 192.168.50.113 (192.168.50.113) 56(84) bytes of data.",
    "..."
  ],
  "traceroute_stdout": [
    "traceroute to 192.168.50.113 (192.168.50.113), 5 hops max",
    "..."
  ]
}
```
