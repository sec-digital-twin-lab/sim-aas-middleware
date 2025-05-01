# Running a Sim-aaS Node Instance
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
