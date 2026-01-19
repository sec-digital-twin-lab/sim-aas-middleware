# Running a Sim-aaS Node Instance
A Sim-aaS Node instance provides services to store data objects and to execute simulation jobs.
These services are provided by the Data Object Repository (DOR) and Runtime Infrastructure (RTI)
modules, respectively. 

### Storage Paths
When starting a node, the user has to specify the datastore path where a node stores all its data,
and the ID of a keystore whose identity the node will use. By default, the datastore path will 
be in the home directory (e.g. `$HOME/.datastore`) and the keystore path to search for the ID in 
the home directory as well (e.g `$HOME/.keystore`). 

### Addresses and Ports
The user has to assign the address and port for the REST and P2P service for the node. 
These addresses are used for nodes in the network to communicate with each other. 
Make sure that the ports being assigned are open and not used by other processes. 
Additionally, new nodes will need to connect to a boot node in the network to retrieve 
information about other nodes in the network. 
The boot node will be referenced by its REST address and can be any node in the network.
If the node that is the first node in the network, it can connect to itself. 

## Storage and Execution Nodes
Nodes can be configured to act as storage-only nodes (by only starting the DOR service),
execution-only nodes (by only starting the RTI service), or as full nodes (by starting DOR
and RTI services).

DOR and RTI services are provided through **plugins**. The available options depend on which
plugins are discovered at startup. Built-in plugins are located in the `plugins/` directory
of the Sim-aaS Middleware repository. Additional plugins can be loaded using the `--plugins`
argument (see below).

If starting a node interactively, the user is prompted to select what DOR service to use:
```
? Select the type of DOR service:
❯ None
  Default
```
Similarly, the user is prompted to select what RTI service to use:
```
? Select the type of RTI service:
❯ None
  Docker
  Aws
```
The available choices are dynamically determined based on discovered plugins.

Depending on the DOR and RTI selection, the node operates in one of four configurations:

| Configuration | DOR | RTI | Description |
|---------------|-----|-----|-------------|
| Full node | Yes | Yes | Provides both storage and execution services |
| Storage node | Yes | No | Provides data storage only |
| Execution node | No | Yes | Provides job execution only |
| Network node | No | No | Provides P2P and network services only |

A **network node** (neither DOR nor RTI) can be useful as a dedicated boot node for network
discovery or for monitoring network health. It provides the P2P interface and maintains
information about the network and known identities.

### Using External Plugins
To load plugins from additional directories, use the `--plugins` argument:
```shell
simaas-cli service --plugins /path/to/my-plugins --plugins /path/to/other-plugins
```
Each plugin directory should contain subdirectories with the plugin implementations
(e.g., `dor_custom/`, `rti_kubernetes/`). See the Developer Documentation for details
on creating custom plugins.

### Other Configurations
There are a number of options (and corresponding command line flags) that the user can specify:
- Retain RTI job history: instructs the RTI to keep the complete job history. This option is
useful for debugging and testing purposes.
- Bind service to all network addresses: Binds the REST and P2P services to any address of the 
machine i.e. `0.0.0.0`. This option is useful for Docker.
- Strict processor deployment: instructs the node to only allow the node owner identity to 
deploy/undeploy processors.

### Example
Here is an example for starting a node using the interactive CLI command:
```shell
simaas-cli service
```

Without specifying any arguments, the command will ask the user to input a number of settings:
```
? Enter path to datastore: /Users/aydth/.datastore
? Select the type of DOR service: Default
? Select the type of RTI service: Docker
? Retain RTI job history? No
? Bind service to all network addresses? No
? Strict processor deployment? Yes
AWS SSH Tunneling information found? NO
? Enter address for REST service: 192.168.50.126:5001
? Enter address for P2P service: tcp://192.168.50.126:4001
? Enter REST address of boot node: 192.168.50.126:5001
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
INFO:     Started server process [48292]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://192.168.50.126:5001 (Press CTRL+C to quit)
INFO:     192.168.50.126:64460 - "GET /api/v1/db/node HTTP/1.1" 200 OK
Created 'default/docker' Sim-aaS node instance at 192.168.50.126:5001/tcp://192.168.50.126:4001 (keep RTI job history: No) (strict: Yes) 
```

The example above shows a node running with a REST service at address `192.168.50.126:5001`. 
This address will be used to interact with this node using the CLI. 

Running a service can also be done non-interactively, by providing the necessary command line
arguments. For a list of all arguments, use:
```shell
simaas-cli service --help
```

### Path to Sim-aaS Middleware Repository
A node may have to build a Processor Docker Image (PDI) from scratch in case the PDI in the DOR
only contains the meta information about GPP but not the image itself. For this reason, either 
the `SIMAAS_REPO_PATH` environment variable needs to be defined, or the path needs to be passed
explicitly when using the CLI command. 


## AWS RTI Service
> Note: if you want to use processors with AWS, make sure you build the Processor Docker
> Images for `linux/amd64`. See documentation [here](./usage_manage_processor_docker_images.md)
> to learn how to do that.

If `aws` is chosen for the RTI service, jobs will be submitted to AWS Batch. In order for this
to work, the AWS environment has to be setup accordingly and the following environment variables
will have to be defined:
- `SIMAAS_AWS_REGION`: the AWS region to be used (e.g., `ap-southeast-1`).
- `SIMAAS_AWS_ROLE_ARN`, `SIMAAS_AWS_ACCESS_KEY_ID`, `SIMAAS_AWS_SECRET_ACCESS_KEY`: use AWS 
IAM to create a user (e.g., `simaas-service` and obtain the ARN and a corresponding access 
key. Recommended permissions required for this role are listed below.
- `SIMAAS_AWS_JOB_QUEUE`: the name of the AWS Batch job queue (e.g., `simaas-queue`). This has
to be obtained by creating a job queue and a corresponding compute environment.

### Required AWS Permissions:
`AmazonEC2ContainerRegistryFullAccess`
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "*"
        }
    ]
}
```
`AmazonECSTaskExecutionRolePolicy`
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "*"
        }
    ]
}
```
Additional selective permissions:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": "logs:CreateLogGroup",
            "Resource": "*"
        }
    ]
}
```
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": [
                "batch:DescribeJobQueues",
                "batch:CancelJob",
                "batch:SubmitJob",
                "batch:ListJobs",
                "batch:DescribeComputeEnvironments",
                "batch:DeregisterJobDefinition",
                "iam:PassRole",
                "batch:DescribeJobs",
                "batch:ListTagsForResource",
                "batch:RegisterJobDefinition",
                "batch:DescribeSchedulingPolicies",
                "batch:DescribeJobDefinitions",
                "batch:ListSchedulingPolicies",
                "batch:GetJobQueueSnapshot"
            ],
            "Resource": "*"
        }
    ]
}
```

### Important Notes on Testing with AWS RTI Service
When running a local node with AWS RTI Service, it is important that the node is on the same
network as the AWS resources. This makes testing difficult. A workaround is using SSH
tunneling to allow jobs running AWS Batch to connect to a Sim-aaS Node that is running 
locally. In order for this to work an AWS EC2 instance is required that is on the same
network as the AWS Batch resources. This EC2 instance needs to be accessible via public
address. The following environment variables need to be set (sample value are shown here,
modify as needed):
```shell
export SSH_TUNNEL_HOST="ec2-aaa-bbb-ccc-ddd.ap-southeast-1.compute.amazonaws.com"
export SSH_TUNNEL_USER="ubuntu"
export SSH_TUNNEL_KEY_PATH="/path/to/key.pem"
```

The `SSH_TUNNEL_HOST` should be set to the **Public IPv4 DNS** of the EC2 instance. The
`SSH_TUNNEL_USER` and `SSH_TUNNEL_KEY_PATH` should be the username and a corresponding
key file of a user account that can login to the EC2 instance. The SSH tunnel will be 
established between the local machine and the EC2 instance using the specified user account.

When using AWS RTI, the node will be referenced to by its domain name which is determined
automatically during runtime. This domain name is important for jobs running on AWS Batch
to connect to the Sim-aaS Node instance via the P2P protocol. However, if we run a node
locally, the domain name will not be reachable from jobs running on AWS Batch. It is thus
necessary to overwrite the domain used by the node. The following environment variable 
needs to be set, pointing at the **Private IPv4 DNS** name of the EC2 instance (sample 
value are shown here, modify as needed):
```shell
export SIMAAS_CUSTODIAN_HOST="ip-aaa-bbb-ccc-ddd.ap-southeast-1.compute.internal"
```

> Note: there is no option to enable SSH tunneling as this is meant as a workaround for 
> development and testing purposes. Instead, SSH tunneling is automatically attempted if
> AWS RTI service is selected **and all required environment variables** are defined.
