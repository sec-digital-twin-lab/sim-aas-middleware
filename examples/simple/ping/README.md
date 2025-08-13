# Ping Example: Processor Testing Outbound Connectivity

This example demonstrates involves a simple processor that tries to test the connectivity
to an external host given its address. The processor will attempt to run 'ping' and 'traceroute'
commands to test the connectivity to the external host.

The example consists of one processor:

- **`ProcessorPing`** - a processor that takes one input, `parameters`, and produces an 
output `result`.

## Install Sim-aaS Middleware
```shell
mkdir ping_test
cd ping_test
python3.12 -m venv venv
source venv/bin/activate
pip install ~/Desktop/repositories/sim-aas-middleware
```


## Setup the test node
Start a new terminal and actiate the environment:
```
source venv/bin/activate
```

Create an identity for testing:
```
simaas-cli identity create
```

Make sure Docker is running before starting the node.

Run a simple node instance:
```
simaas-cli service
```

Here is sample output of the terminal for reference:
```
? Enter path to datastore: /Users/foobar/.datastore
? Enter the path to the sim-aas-middleware repository /Users/foobar/Desktop/repositories/sim-aas-middleware
? Select the type of DOR service: Basic
? Select the type of RTI service: Docker
? Retain RTI job history? Yes
? Bind service to all network addresses? No
? Strict processor deployment? No
AWS SSH Tunneling information found? NO
? Enter address for REST service: 192.168.50.117:5001
? Enter address for P2P service: tcp://192.168.50.117:4001
? Enter REST address of boot node: 192.168.50.117:5001
? Select the keystore: ping_test/email/iq37rd6k47f1sgdjy1sw1lh81kjh4j0nalhrberox8srldkaid3i2b902w56qdxn
? Enter password: ****
INFO:     Started server process [76459]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://192.168.50.117:5001 (Press CTRL+C to quit)
INFO:     192.168.50.117:51605 - "GET /api/v1/db/node HTTP/1.1" 200 OK
Created 'basic/docker' Sim-aaS node instance at 192.168.50.117:5001/tcp://192.168.50.117:4001(keep RTI job history: Yes) (strict: No) 
```

Keep that terminal open. Don't terminate it.


## Build and deploy the ping processor
Start a new terminal and actiate the environment:
```
source venv/bin/activate
```

Navigate to the folder where the processor is located:
```
cd ~/Desktop/repositories/sim-aas-middleware/examples/simple/ping
```

Run the build command:
```
simaas-cli build-local --arch=linux/amd64 .
```

Here is sample output of the terminal for reference:
```
? Enter the target node's REST address 192.168.50.117:5001
? Select the keystore: ping_test/email/iq37rd6k47f1sgdjy1sw1lh81kjh4j0nalhrberox8srldkaid3i2b902w56qdxn
? Enter password: ****
? Enter the path to the sim-aas-middleware repository /Users/foobar/Desktop/repositories/sim-aas-middleware
Using processor path '/Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple/ping'.
Not using any GitHub credentials.
Processor content hash: e8c8ce1102ffc23ff0af8bcd63c8cff67f150fd6056b74d5b218fa40611f97ee
Building image 'foobar/proc-ping:e8c8ce1102ffc23ff0af8bcd63c8cff67f150fd6056b74d5b218fa40611f97ee' for platform 'linux/amd64'. This may take a while...
Done building image 'foobar/proc-ping:e8c8ce1102ffc23ff0af8bcd63c8cff67f150fd6056b74d5b218fa40611f97ee'.
Done exporting image to '/var/folders/p3/yjbdnj8n69d_dmzpw51dmtj40000gp/T/tmpywhk04_0/image.tar'.
Done uploading image to DOR -> object id: 299acc4b2bcd58c28c32bde1749d0d02e15abd8c392d89476eb89f79837fad5b
```

Deploy the processor:
```
simaas-cli rti proc deploy
```

Here is sample output of the terminal for reference:
```
? Enter the node's REST address 192.168.50.117:5001
? Select the keystore: ping_test/email/iq37rd6k47f1sgdjy1sw1lh81kjh4j0nalhrberox8srldkaid3i2b902w56qdxn
? Enter password: ****
? Select the processor you would like to deploy: proc-ping <299a...ad5b> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple:e8c8ce...
Deploying processor 299a...ad5b...Done
```


## Run a job
Start a new terminal and actiate the environment if necessary:
```
source venv/bin/activate
```

Submit a job:
```
simaas-cli rti job submit
```

Note the inputs for the interactive command, in particular the "address" field in the input JSON object. Change the address as required.

Here is sample output of the terminal for reference:
```
? Select the keystore: ping_test/email/iq37rd6k47f1sgdjy1sw1lh81kjh4j0nalhrberox8srldkaid3i2b902w56qdxn
? Enter password: ****
? Enter the node's REST address 192.168.50.117:5001
? Select the processor for this task: 299a...ad5b: proc-ping [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/simple@e8c8ce..
.
Specify input interface item 'parameters' with data type/format JSONObject/json
? How to specify? by-value
JSON schema available:  no
? Enter a valid JSON object: {"address": "192.168.50.113", "do_ping": true, "do_traceroute": true}
? Select the owner for the output data objects: iq37rd6k47f1sgdjy1sw1lh81kjh4j0nalhrberox8srldkaid3i2b902w56qdxn - ping_test <email>
? Select the destination node for the output data objects: iq37...qdxn: ping_test at 192.168.50.117:5001
? Should access to output data objects be restricted? No
? Give the task a name: test1
? Select a budget for this task: 1 vCPUs, 2048 GB RAM memory
? Add another task? Note: multiple tasks submitted together will be executed as batch. No
Job submitted: aoLmu4GR
```

Check if job is done:
```
simaas-cli rti job status aoLmu4GR
```

Here is sample output (truncated) of the terminal for reference:
```
Job status:
{
    "state": "successful",
    "progress": 100,
    "output": {
        "result": {
            "obj_id": "684ce43ed69ca440c8a20ee7ad3d44a94490c886ff1bf9f3517c6a392629125c",
            "c_hash": "9e3b034900f4aff669b54c56d9b8e32ae518b7b26d3e566ebee2dd198af0c118",
            "data_type": "JSONObject",
            "data_format": "json",
    ...
```

Look for "state" in the output. It should show "successful".

Fetch the results:
```
simaas-cli dor download
```

Here is sample output of the terminal for reference:
```
? Enter the destination folder /Users/foobar/Desktop
? Enter the node's REST address 192.168.50.117:5001
? Select the keystore: ping_test/email/iq37rd6k47f1sgdjy1sw1lh81kjh4j0nalhrberox8srldkaid3i2b902w56qdxn
? Enter password: ****
? Select data object to be downloaded: ['684c...125c [JSONObject:json] name=result job_id=aoLmu4GR']
Downloading 684c...125c to /Users/foobar/Desktop/684ce43ed69ca440c8a20ee7ad3d44a94490c886ff1bf9f3517c6a392629125c.json ...Done
```

Have a look at the results:
```
cat /Users/foobar/Desktop/684ce43ed69ca440c8a20ee7ad3d44a94490c886ff1bf9f3517c6a392629125c.json
```

Here is sample output of the terminal for reference:
```
{
  "ping_stdout": [
    "PING 192.168.50.113 (192.168.50.113) 56(84) bytes of data.",
    "64 bytes from 192.168.50.113: icmp_seq=1 ttl=63 time=9.10 ms",
    "64 bytes from 192.168.50.113: icmp_seq=2 ttl=63 time=15.1 ms",
    "64 bytes from 192.168.50.113: icmp_seq=3 ttl=63 time=10.6 ms",
    "64 bytes from 192.168.50.113: icmp_seq=4 ttl=63 time=14.8 ms",
    "",
    "--- 192.168.50.113 ping statistics ---",
    "4 packets transmitted, 4 received, 0% packet loss, time 3016ms",
    "rtt min/avg/max/mdev = 9.095/12.394/15.109/2.620 ms",
    ""
  ],
  "traceroute_stdout": [
    "traceroute to 192.168.50.113 (192.168.50.113), 5 hops max",
    "  1   172.17.0.1  0.019ms  0.001ms  0.001ms ",
    "  2   *  *  * ",
    "  3   *  *  * ",
    "  4   *  *  * ",
    "  5   *  *  * ",
    ""
  ]
}
```
