# Simulation-as-a-Service (Sim-aaS) Middleware
The Sim-aaS Middleware provides the infrastructure to facilitate deployment and operations
of federations of computational models.

## Prerequisites
- Python 3.12
- Linux or MacOS operating system (not tested with Windows)

## Install

Clone the repository:
```shell
git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware
```

Create and activate the virtual environment:
```shell
python3.12 -m venv venv
source venv/bin/activate
```

Install the Sim-aaS Middleware:
```shell
pip install sim-aas-middleware
```

Once done, you may deactivate the virtual environment - or keep it activated if you want
to starting using the Sim-aaS Middleware:
```shell
deactivate
```

## Usage
The Sim-aaS Middleware can be used via a Command Line Interface (CLI) with this command: 
```shell
simaas-cli
```

The CLI can be used in a non-interactive manner by providing corresponding command line
arguments. In addition, commands also allow interactive use of the CLI in which case the 
user is prompted for input. The following sections explains how to use of the CLI for 
common use-cases.

### Create Identity
> *If you are using the Sim-aaS Middleware for the first time, you need to create an identity.*

Identities are used across the Sim-aaS system for authentication/authorisation purposes as well 
as for managing ownership and access rights to data objects. An identity is required to operate 
Sim-aaS node instances or to interact with remote instances.

To create an identity, the user has to provide a name for the identity, a contact (i.e.,
email) and a password. In addition to a name and email, an identity is also associated with a 
set of keys for signing and encryption purposes, which are generated upon creation of the 
identity. The identity would then be assigned a unique ID and be stored together with the set 
of keys in the form of a JSON file called a keystore. The keystore can be referenced by the 
identity ID.

By default, the keystore will be created in a folder named `.keystore` in the home directory
(e.g. `$HOME\.keystore`), and can be changed by providing the `--keystore` flag.

Identities can be created interactively by following the prompts using:
```shell
simaas-cli identity create

? Enter name: foo bar
? Enter email: foo.bar@email.com
? Enter password: ****
? Re-enter password: ****
New keystore created!
- Identity: foo bar/foo.bar@email.com/i6vmw1hffcsf5pg6dlc4ofxl1s95czqs6uuqg8mf9hz32qdei4b8gmwu4eivtm3t
- Signing Key: EC/secp384r1/384/466de4a0abd4275c5efeba80ef0e3ec7f65c3fa5849160d6b2ad79c1329bcedb
- Encryption Key: RSA/4096/42cd241bb8ac7dc1864350e9fc47cdb1833314dd35d01c46455ea681f552f165
```

The example above shows the identity created with ID `i6vmw1hffcsf5pg6dlc4ofxl1s95czqs6uuqg8mf9hz32qdei4b8gmwu4eivtm3t`.

Identities can also be created non-interactively by specifying the password as well as details
about the identity using command line parameters. For example:
```shell
simaas-cli --keystore=$KEYSTORE_PATH --password 'password' identity create --name 'foo bar' --email 'foo.bar@email.com'
```

After creating identities, the user can list all the keystores found in the keystore path using:
```shell
simaas-cli identity list

Found 1 keystores in '/home/foo.bar/.keystore':
NAME     EMAIL              KEYSTORE/IDENTITY ID
----     -----              --------------------
foo bar  foo.bar@email.com  i6vmw1hffcsf5pg6dlc4ofxl1s95czqs6uuqg8mf9hz32qdei4b8gmwu4eivtm3t
```

The `--keystore` flag can be provided to the command above if it is not found in the default path.

#### Credentials
The keystore can also be used to store and associate credentials with the identity. 
These credentials can be used for deploying processors and running jobs.
For example, GitHub for cloning from private repositories or SSH for executing remote commands.
More information about deploying processors and running jobs can be found in the sections below.

Credentials (SSH or GitHub) can be added by following the prompts using:
```shell
simaas-cli identity credentials add ssh

# OR

simaas-cli identity credentials add github
```

For a list of all commands concerning identities, use:
```shell
simaas-cli identity --help
```

### Running a Sim-aaS Node Instance
A Sim-aaS Node instance provides services to store data objects and to execute simulation jobs.
These services are provided by the Data Object Repository (DOR) and Runtime Infrastructure (RTI)
modules, respectively. Depending on the requirements, nodes can be configured to act as 
storage-only nodes (by only starting the DOR service), execution-only nodes (by only starting 
the RTI service), or as full nodes (by starting DOR and RTI services). 

When starting a node, the user has to specify the datastore path where a node stores all its data,
and the ID of a keystore whose identity the node will use. By default, the datastore path will 
be in the home directory (e.g. `$HOME/.datastore`) and the keystore path to search for the ID in 
the home directory as well (e.g `$HOME/.keystore`). 

The user has to assign the address and port for the REST and P2P service for the node. 
These addresses are used for nodes in the network to communicate with each other. 
Make sure that the ports being assigned are open and not used by other processes. 
Additionally, new nodes will need to connect to a boot node in the network to retrieve 
information about other nodes in the network. 
The boot node will be referenced by its REST address and can be any node in the network.
If the node that is the first node in the network, it can connect to itself. 

There are a number of options (and corresponding command line flags) that the user can specify:
- Retain RTI job history: instructs the RTI to keep the complete job history. This option is
useful for debugging and testing purposes.
- Bind service to all network addresses: Binds the REST and P2P services to any address of the 
machine i.e. `0.0.0.0`. This option is useful for Docker.
- Strict processor deployment: instructs the node to only allow the node owner identity to 
deploy/undeploy processors.
- Concurrent job processing: instructs the RTI to process all jobs in the queue concurrently.
- Purge inactive jobs: instructs the RTI to purge inactive jobs upon startup. This can be useful
to get rid of stale jobs.

Here is an example:
```shell
simaas-cli service

? Enter path to datastore: /Users/foo_bar/.datastore
? Enter address for REST service: 192.168.50.126:5001
? Enter address for P2P service: tcp://192.168.50.126:4001
? Enter REST address of boot node: 192.168.50.126:5001
? Select the type of service: Full node (i.e., DOR + RTI services)
? Retain RTI job history? No
? Bind service to all network addresses? No
? Strict processor deployment? Yes
? Concurrent job processing? Yes
? Purge inactive jobs? No
? Select the keystore: foo bar/foo.bar@email.com/i6vmw1hffcsf5pg6dlc4ofxl1s95czqs6uuqg8mf9hz32qdei4b8gmwu4eivtm3t
? Enter password: ****
INFO:     Started server process [19547]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://192.168.50.126:5001 (Press CTRL+C to quit)
```

The example above shows a node running with a REST service at address `192.168.50.126:5001`. 
This address will be used to interact with this node using the CLI. 

Running a service can also be done non-interactively, by providing the necessary command line
arguments. For a list of all arguments, use:
```shell
simaas-cli service --help
```

### Adding and Removing a Data Object
One of the two core modules of a Sim-aaS Node is the Data Object Repository (DOR). It stores data
objects and makes them available across the domain for jobs that are executed by a Runtime 
Infrastructure (RTI). The content of a data object can be virtually anything so as long as it
is a single file.

When adding a new data object to a DOR, the user needs to specify the data type and format of
the data object. In addition, the user may use optional flags to indicate if access to the data 
object should be restricted (`--restrict-access`) and if the data object content should be 
encrypted (`--encrypt-content`). If access is restricted, the owner needs to explicitly grant
permission to other identities before they can make use of the data objects. If encryption is 
used, the CLI will use keystore functionality to create a content key and encrypt the data 
object before uploading it to the DOR. 

If it is the first time an identity is interacting with the node, there will be an option to 
publish the identity to the node so that the node will recognise it. This allows a user to 
assign this new identity as a co-creator of the object (more information below).

Example (the data objects used here can be found under `/examples/adapters/proc_example/data`
in this repository):
```shell
simaas-cli dor --address 192.168.50.126:5001 add --data-type 'JSONObject' --data-format 'json' data_object_a.json

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select all identities that are co-creators of this data object: ['test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq']
Data object added: {
    "obj_id": "9f5b326d9ca6af23380afc490742c65163439556c70f44db804b907cf2e9a85c",
    "c_hash": "05ea03c4a3ef474e2276a2910f804ae49e759d1107946e8b50df38dc508abc27",
    "data_type": "JSONObject",
    "data_format": "json",
    "created": {
        "timestamp": 1730245015966,
        "creators_iid": [
            "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq"
        ]
    },
    "owner_iid": "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq",
    "access_restricted": false,
    "access": [
        "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq"
    ],
    "tags": {
        "name": "data_object_a.json"
    },
    "last_accessed": 1730245015993,
    "custodian": {
        "identity": {
            "id": "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq",
            "name": "test",
            "email": "test@email.com",
            "s_public_key": "e4a9a27f8e15dd3b08cf994edc2f5231d5cf56562a19fffec8fbfc8469651c3c",
            "e_public_key": "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEApaacUBJBdmkQvp8LWJsZrBPGCMPr6jiYQAhp3eKDjaq4VBZmSnV5GZaetPu/hRRnaOjL3IfQ8WfJzjI0sW+7liEWCEiSZy7sWq6MJXXcmxkUihCsMx70tgSPXa1JTFYoI3LlxHVt/TSShBHDH33a5H/Q80wWvXj0EmKI0ETDYOsCekIusxhQkCUovrBhnwM8s4QWjWhOOBkkQ5+RpVwXnm4XKTgXwQrRk3Rg3MXhKd32LXTIR0E49HAJKm+wQuLfejSJGvH36vQQ+98Lh6BmGuVx8128eGf8FG0iJxeE1ulgxjm+3NrKKqWg9nc0H4Up71Syky3BGXS9WPsjNq+00CIUGmA1caw3hKSJGcOI5cHHZxHjBna/OLc722WwmH/yCn5GrS8XNQ594XRO/U7vDPq1dUZHNy6JZrLof4hqd+zGgWMhayxBZ/ej5yVufkme2mOfme5ZQM2XWaE+Auj+6za7WfMgrEEY9rqJW9S6HzK3nlscNGXfEXKv63fNjzdf4P9Q6HsQbRgam/8tbBdiTaHWqkSleoulolCIAgNgIRdvpVYCzxf8yKXPMb7u9VbxrP9IOvQWZFrRteTtE+3vZRE6k1zxSlFTfd4A5SXy5t4srmQ+qb2KYiOY3Iak5lxN3U16akXjk+Q7HM3MZ1js4fZRCBU6k9opgM+2e36VY4ECAwEAAQ==",
            "c_public_key": "5e55577b3921507a3c4d516f3475444f45774a794f305e297b4d65385b3e485d6a2175512e345d62",
            "nonce": 2,
            "signature": "f08dec6a1fda1b852b0f1d11f2f0343be3c87a32d11aebf18db7a47cdfc2e2b0643a62b41a22d40eb41d26259244260606f128eafe7717d275e75cab4760180d",
            "last_seen": 1730244334803
        },
        "last_seen": 1730245015996,
        "dor_service": true,
        "rti_service": true,
        "p2p_address": "tcp://192.168.50.126:4001",
        "rest_address": [
            "192.168.50.126",
            5001
        ],
        "retain_job_history": false,
        "strict_deployment": false,
        "job_concurrency": true
    },
    "content_encrypted": false,
    "license": {
        "by": false,
        "sa": false,
        "nc": false,
        "nd": false
    },
    "recipe": null
}
```
The example above shows the new data object `9e367145e8f587f7097413b27c856d4b3fa40d6469548179fbbcb4379d519f24` 
with an owner ID `ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq` which belongs to the identity
used to add the data object.

Data objects can only be removed by their owner. Example:
```shell
simaas-cli dor --address 192.168.50.126:5001 remove                                                              
? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select data objects to be removed: 
❯ ○ 9f5b...a85c [JSONObject:json] name=data_object_a.json
```

If the data object `ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq` would not be
owned by the identity used to run the CLI, the object would not be available for selection. If the 
object id used as command line argument, the DOR would deny to delete it if the user is not the
owner of the data object.

### Granting and Revoking Access to Data Objects 
If the access to a data object is restricted (see previous section), then only identities that
have been explicitly granted permission may use the data object. To grant access:
```shell
simaas-cli dor --address 192.168.50.126:5001 access grant

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select data objects: ['9f5b...a85c [JSONObject:json] name=data_object_a.json']
? Select the identity who should be granted access: gosigg6nq3n89528w7lm5vqt07ookb1ort30klil8xzhz9mkff5aooanj5awsjtk - foo bar <for.bar@email.com>
Granting access to data object 9f5b...a85c for identity gosi...sjtk...Done
```

Note that a DOR can only grant access to identities it knows about. If you have created a new
identity, then the network of Sim-aaS nodes doesn't automatically know about this identity. You
can publish an identity to a node, using:
```shell
simaas-cli identity publish
```

To revoke access:
```shell
sim-aas-middleware % simaas-cli dor --address 192.168.50.126:5001 access revoke

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select data object: 9f5b...a85c [JSONObject:json] name=data_object_a.json
? Select the identities whose access should be removed: ['gosigg6nq3n89528w7lm5vqt07ookb1ort30klil8xzhz9mkff5aooanj5awsjtk - foo bar <for.bar@email.com>']
Revoking access to data object 9f5b...a85c for identity gosi...sjtk...Done
```

### Building a Processor Docker Image (PDI)
The other core module of a Sim-aaS Node is the Runtime Infrastructure (RTI). It executes 
computational jobs using processors that have been deployed on the node. Depending on the 
processor, a job will use some input data (provided by a DOR in form of data objects
or parameters in form of a json object) and produce some output data (as data objects that will
be stored on a DOR). Exactly what input is consumed and what output is produced is specified 
by the descriptor of the processor. See "Processor" for more information on this topic. 

Before a processor can be deployed, it has to be built first and uploaded to the DOR in form
of a Processor Docker Image (PDI). Basically, a PDI is a Docker image of the processor.
There are two CLI commands that can be used to build a PDI:
`build-local` and `build-github` depending on whether the source of the processor can be
found locally or in a Github repository.

Example for building a PDI from a local source:
```shell
sim-aas-middleware % simaas-cli build-local examples/adapters/proc_example

? Enter the target node's REST address 192.168.50.126:5001
? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
Using processor path '/Users/aydth/Desktop/repositories/sim-aas-middleware/examples/adapters/proc_example'.
Using GitHub credentials from env for user ''.
Processor content hash: cb9e3dceca3f4270106c3aa9fe2bcf7351fd9b8b521f748ed79fb6782022ce38
Building image 'aydth/proc_example/example-processor:cb9e3dceca3f4270106c3aa9fe2bcf7351fd9b8b521f748ed79fb6782022ce38'. This may take a while...
Done building image 'aydth/proc_example/example-processor:cb9e3dceca3f4270106c3aa9fe2bcf7351fd9b8b521f748ed79fb6782022ce38'.
Done exporting image to '/var/folders/hj/sgm_4v_90q1899r6fv65y14c0000gq/T/tmp_9nskf0q/image.tar'.
Done uploading image to DOR -> object id: 06f69d94fd03c1972383dae133feddee23167ab5f3a807bb3e36827e324308ca
```

Example for building a PDI from a Github source:
```shell
simaas-cli build-github                              

? Enter the target node's REST address 192.168.50.126:5001
? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Enter URL of the repository: https://github.com/sec-digital-twin-lab/sim-aas-middleware
? Enter the commit id: dev-1.0.0
? Enter path to the processor: examples/adapters/proc_example
Using repository at https://github.com/sec-digital-twin-lab/sim-aas-middleware with commit id dev-1.0.0.
Using processor path 'examples/adapters/proc_example'.
Using GitHub credentials from env for user ''.
Done cloning https://github.com/sec-digital-twin-lab/sim-aas-middleware.
Building image 'sec-digital-twin-lab/sim-aas-middleware/example-processor:33d795ecbd53eae4c4f9ef3c9d7a8e90821a0053'. This may take a while...
Done building image 'sec-digital-twin-lab/sim-aas-middleware/example-processor:33d795ecbd53eae4c4f9ef3c9d7a8e90821a0053'.
Done uploading PDI to DOR -> object id: d899526f24ea732482f040cdbda4ebf72cab0e46c936a6a729e476565bcc7493
```

Note that if the repository is not public it is necessary to specify the necessary
Github credentials `GITHUB_USERNAME` and `GITHUB_TOKEN` either in a `.env` file or
directly as environment variables.

### Deploying and Undeploying Processors
Once the PDI available in a DOR, the RTI can be instructed to deploy the processor on 
the node. Example:
```shell
simaas-cli rti --address 192.168.50.126:5001 proc deploy

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select the processor you would like to deploy: example-processor <06f6...08ca> local:///Users/aydth/Desktop/repositories/sim-aas-middleware/examples/adapters:
cb9e3d...
Deploying processor 06f6...08ca...Done
```

Once deploy, you can display details about the processor:
```shell
simaas-cli rti --address 192.168.50.126:5001 proc show

? Select the processor: 06f6...08ca: example-processor [State.READY] local:///Users/aydth/Desktop/repositories/sim-aas-middleware/examples/adapters@cb9e3d...
Processor Information:
- Id: 06f69d94fd03c1972383dae133feddee23167ab5f3a807bb3e36827e324308ca
- State: State.READY
- Name: example-processor
- Image: aydth/proc_example/example-processor:cb9e3dceca3f4270106c3aa9fe2bcf7351fd9b8b521f748ed79fb6782022ce38
- Input:
   a -> JSONObject:json
   b -> JSONObject:json
- Output:
   c -> JSONObject:json
- Error: (none)
- Jobs: (none)
```

Undeployment works in the same fashion as deployment:
```shell
simaas-cli rti --address 192.168.50.126:5001 proc undeploy

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select the processor: ['06f6...08ca: example-processor [State.READY] local:///Users/aydth/Desktop/repositories/sim-aas-middleware/examples/adapters@cb9e3d...'
]
Undeploying processor 06f69d94fd03c1972383dae133feddee23167ab5f3a807bb3e36827e324308ca...Done
```

For a list of all arguments, use:
```shell
simaas-cli rti proc --help
```

### Submit Job and Check Status
Once a processor is deployed, it can be used to perform computational jobs. For all items 
in the processor's input interface, a corresponding data object needs to be provided either
by-reference (i.e., using the id of a data object stored in a DOR) or by-value (i.e., by 
directly providing the value for the input item as `json` object). For all items in the 
processor's output interface, a job needs to specify the future owner of the data object once
it has been produced, whether it should have restricted access and whether it should be 
encrypted.

```shell
simaas-cli rti --address 192.168.50.126:5001 job submit

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select the processor for the job: 06f6...08ca: example-processor [State.READY] local:///Users/aydth/Desktop/repositories/sim-aas-middleware/examples/adapters@
cb9e3d...
Specify input interface item 'a' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'a': 9f5b...a85c [JSONObject:json] name=data_object_a.json
Specify input interface item 'b' with data type/format JSONObject/json
? How to specify? by-reference
? Select the data object to be used for input 'b': 9e36...9f24 [JSONObject:json] name=data_object_b.json
? Select the owner for the output data objects: ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq - test <test@email.com>
? Select the destination node for the output data objects: ls1v...9shq: test at 192.168.50.126:5001
? Should access to output data objects be restricted? No
Job submitted: 50U0yUWC
```

If the job has been successfully submitted, a job id will be returned. This id can be used
to check on the status of the job:
```shell
simaas-cli rti --address 192.168.50.126:5001 job status 50U0yUWC

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
Job status:
{
    "state": "successful",
    "progress": 100,
    "output": {
        "c": {
            "obj_id": "bd5a12ff9165bb92a95492c7a0e48e074df9a21ac4b0fd012cd6c6184e42a3f9",
            "c_hash": "2b3f0ceba8a3cdd1fce97947fe2a21e77033798f99bf2a8df0d9f2f3aa567c30",
            "data_type": "JSONObject",
            "data_format": "json",
            "created": {
                "timestamp": 1730327474304,
                "creators_iid": [
                    "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq"
                ]
            },
            "owner_iid": "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq",
            "access_restricted": false,
            "access": [
                "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq"
            ],
            "tags": {
                "name": "c",
                "job_id": "50U0yUWC"
            },
            "last_accessed": 1730327474304,
            "custodian": {
                "identity": {
                    "id": "ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq",
                    "name": "test",
                    "email": "test@email.com",
                    "s_public_key": "e4a9a27f8e15dd3b08cf994edc2f5231d5cf56562a19fffec8fbfc8469651c3c",
                    "e_public_key": "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEApaacUBJBdmkQvp8LWJsZrBPGCMPr6jiYQAhp3eKDjaq4VBZmSnV5GZaetPu/hRRnaOjL3IfQ8WfJzjI0sW+7liEWCEiSZy7sWq6MJXXcmxkUihCsMx70tgSPXa1JTFYoI3LlxHVt/TSShBHDH33a5H/Q80wWvXj0EmKI0ETDYOsCekIusxhQkCUovrBhnwM8s4QWjWhOOBkkQ5+RpVwXnm4XKTgXwQrRk3Rg3MXhKd32LXTIR0E49HAJKm+wQuLfejSJGvH36vQQ+98Lh6BmGuVx8128eGf8FG0iJxeE1ulgxjm+3NrKKqWg9nc0H4Up71Syky3BGXS9WPsjNq+00CIUGmA1caw3hKSJGcOI5cHHZxHjBna/OLc722WwmH/yCn5GrS8XNQ594XRO/U7vDPq1dUZHNy6JZrLof4hqd+zGgWMhayxBZ/ej5yVufkme2mOfme5ZQM2XWaE+Auj+6za7WfMgrEEY9rqJW9S6HzK3nlscNGXfEXKv63fNjzdf4P9Q6HsQbRgam/8tbBdiTaHWqkSleoulolCIAgNgIRdvpVYCzxf8yKXPMb7u9VbxrP9IOvQWZFrRteTtE+3vZRE6k1zxSlFTfd4A5SXy5t4srmQ+qb2KYiOY3Iak5lxN3U16akXjk+Q7HM3MZ1js4fZRCBU6k9opgM+2e36VY4ECAwEAAQ==",
                    "c_public_key": "5e55577b3921507a3c4d516f3475444f45774a794f305e297b4d65385b3e485d6a2175512e345d62",
                    "nonce": 2,
                    "signature": "f08dec6a1fda1b852b0f1d11f2f0343be3c87a32d11aebf18db7a47cdfc2e2b0643a62b41a22d40eb41d26259244260606f128eafe7717d275e75cab4760180d",
                    "last_seen": 1730244334803
                },
                "last_seen": 1730327474322,
                "dor_service": true,
                "rti_service": true,
                "p2p_address": "tcp://192.168.50.126:4001",
                "rest_address": [
                    "192.168.50.126",
                    5001
                ],
                "retain_job_history": false,
                "strict_deployment": false,
                "job_concurrency": true
            },
            "content_encrypted": false,
            "license": {
                "by": false,
                "sa": false,
                "nc": false,
                "nd": false
            },
            "recipe": {
                "processor": {
                    "repository": "local:///Users/aydth/Desktop/repositories/sim-aas-middleware/examples/adapters",
                    "commit_id": "cb9e3dceca3f4270106c3aa9fe2bcf7351fd9b8b521f748ed79fb6782022ce38",
                    "proc_path": "proc_example",
                    "proc_descriptor": {
                        "name": "example-processor",
                        "input": [
                            {
                                "name": "a",
                                "data_type": "JSONObject",
                                "data_format": "json",
                                "data_schema": {
                                    "type": "object",
                                    "properties": {
                                        "v": {
                                            "type": "number"
                                        }
                                    },
                                    "required": [
                                        "v"
                                    ]
                                }
                            },
                            {
                                "name": "b",
                                "data_type": "JSONObject",
                                "data_format": "json",
                                "data_schema": {
                                    "type": "object",
                                    "properties": {
                                        "v": {
                                            "type": "number"
                                        }
                                    },
                                    "required": [
                                        "v"
                                    ]
                                }
                            }
                        ],
                        "output": [
                            {
                                "name": "c",
                                "data_type": "JSONObject",
                                "data_format": "json",
                                "data_schema": {
                                    "type": "object",
                                    "properties": {
                                        "v": {
                                            "type": "number"
                                        }
                                    },
                                    "required": [
                                        "v"
                                    ]
                                }
                            }
                        ]
                    }
                },
                "consumes": {
                    "a": {
                        "c_hash": "05ea03c4a3ef474e2276a2910f804ae49e759d1107946e8b50df38dc508abc27",
                        "data_type": "JSONObject",
                        "data_format": "json",
                        "content": null
                    },
                    "b": {
                        "c_hash": "08a9c94164142506e28489a4d6d5d451d01169b7dcfd678dd9c03c6bbedb5194",
                        "data_type": "JSONObject",
                        "data_format": "json",
                        "content": null
                    }
                },
                "product": {
                    "c_hash": "2b3f0ceba8a3cdd1fce97947fe2a21e77033798f99bf2a8df0d9f2f3aa567c30",
                    "data_type": "JSONObject",
                    "data_format": "json",
                    "content": null
                },
                "name": "c"
            }
        }
    },
    "notes": {},
    "errors": [],
    "message": {
        "severity": "info",
        "content": "...and we are done!"
    }
}
```

For a list of all arguments, use:
```shell
simaas-cli rti job --help
```

## Processors, API and SDK
A processor provides a wrapper interface around an application (e.g. program or script) with a 
clearly defined specification of inputs/outputs and instructions on how to install/execute it.
They can be then deployed using the RTI and users would be able to execute computational jobs. 

### Directory Structure
A valid processor should follow a similar folder structure and contain these types of files 
(with exact file names and in the same directory) as shown below:
```
processor/
├── descriptor.json
├── processor.py
└── Dockerfile
```

#### Processor Descriptor (`descriptor.json`)
A processor descriptor specifies the name, input/output interfaces and configurations of a 
processor. It is in the form of a JSON file and is structured as follows:
```json
{
  "name": ...,
  "input": [
    ...
  ],
  "output": [
    ...
  ]
}
```

The input/output interfaces (`input` and `output`) are lists of items that specify the input
data consumed and output data produced by the processor, respectively. This information is used 
before and after job execution to verify that the correct data objects are submitted and 
created respectively.

Structure of Input/Output Item:
```json
{
  "name": ...,
  "data_type": ...,
  "data_format": ...,
  "data_schema": ...
}
```
An item has a name, a data type, a data format, and an optional data schema. 
`data_type` and `data_format` indicate the type and format of the data. Note that
these are application specific. The Sim-aaS Middleware does not interpret the data
and, as such, does not use data type and format information for anything other than
checking if a given data object matches the type and format required by a processor.
The only exception is the case of `data_format = 'json'` in which case the optional
`data_schema` is applied to verify the content. 

Example of a processor descriptor:
```json
{
  "name": "test-proc",
  "input": [
    {
      "name": "a",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    },
    {
      "name": "b",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    }
  ],
  "output": [
    {
      "name": "c",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    }
  ]
}
```

The name of input and output items are used as filenames by the RTI. The string 
used in the `name` field therefore *must not* contain any characters that are 
invalid for the use as filename. In addition, and by convention only, the name 
*should* use snake notation / underscore notation, *should not* include any file 
extension, and *should not* use capitalisation. For example, a name for a data 
object should be 'ah-profile' or 'ah_profile' and not 'AH-profile.json' or 
'AHProfile.csv'. In particular the use of file extensions as part of the `name` 
field is strongly discouraged as the data type and format are explicitly defined 
by the `data_type` and `data_format` fields. In addition, the format of a data
object *should not* be inferred from the name of an input/output item as 
converters may convert a data object from one format to another. This would be 
reflected in the `data_format` field while the `name` remains unchanged and 
become inconsistent/confusing.


#### Processor Implementation (`processor.py`)

The processor implementation has to be located in the `processor.py` file. It needs
to contain a class inheriting from `simaas.core.processor.ProcessorBase`. There is
no limitation as to how many Python files and dependencies a processor  may use. 
However, the convention is that the processor class implementation is located in 
`processor.py`. A valid processor must implement the relevant abstract methods defined
by the `ProcessorBase` class: `run()` and `interrupt()`. The `run()` method will be
called to trigger the processor to begin processing a job. The `interrupt()` method 
may be called to instruct the processor to stop processing. 

The RTI does not make any assumptions as to how these methods are implemented, except
that `run()` is a blocking call which will return once the processor has finished 
processing the job (successfully or not), and that `interrupt()` is a non-blocking
call. There is also no expectation that the processor will have terminated by the
time `interrupt()` returns. Instead, the assumption is that a processor will begin
to gracefully terminate once receiving an interrupt signal. Developers of processors
should keep in mind that the RTI might forcefully terminate a processor at any 
point in time for any reason.

The `run()` method expects three arguments: `wd_path:`, `listener`, and `logger`. A
processor may use the working directory `wd_path` to store the output data as well
as any intermediate data needed to process a job. The RTI will delete the working
directory and its contents upon completion of the job. The `listener` object is of
type `simaas.core.processor.ProgressListener` and allows the processor to inform the
RTI about the current progress `on_progress_update()`, log messages `on_message()`
and availability of output data objects `on_output_available()`. 

Progress updates and messages are meant for the benefit of the application layer, 
not the RTI. As such they are not strictly necessary but strongly encouraged. 
Availability of output data objects, on the other hand, are *mandatory*. A processor 
*must* inform the RTI about output data objects as soon as they are available,
indicating the *exact* name of the output data object (as specified by the processor
descriptor). Output data objects have to be stored in the working directory and 
*must* be named exactly as specified in the processor descriptor (i.e., no added 
file extensions or mismatching capitalisation). Once informed, the RTI will push 
the data object to the designated DOR. For example, if the name of the output is
'ah_profile', then a file with exactly this name needs to exist in the working
directory before informing the RTI by calling 
`listener.on_output_available('ah_profile')`. 


#### Processor Dockerfile

The Dockerfile is used to build the Processor Docker Image (PDI). Essentially, it
provides the instructions to build a Docker image that contains the processor and
defines the entry point as required by the RTI. The contents of the Dockerfile can
be customised as needed for the processor to work. However, the Dockerfile *must*
expose port 5000 (needed by the processors REST interface) and use the CLI `run` 
command as entry point:

```
EXPOSE 5000

ENTRYPOINT ["venv/bin/saas-cli", "run", "--job-path", "/job", "--proc-path", "/processor", "--rest-address", "0.0.0.0:5000"]
```

