# Deploying and Undeploying Processors
Once the PDI available in a DOR, the RTI can be instructed to deploy the processor on 
the node. Example:
```shell
simaas-cli rti --address 192.168.50.126:5001 proc deploy

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select the processor you would like to deploy: example-processor <06f6...08ca> local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/adapters:
cb9e3d...
Deploying processor 06f6...08ca...Done
```

Once deploy, you can display details about the processor:
```shell
simaas-cli rti --address 192.168.50.126:5001 proc show

? Select the processor: 06f6...08ca: example-processor [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/adapters@cb9e3d...
Processor Information:
- Id: 06f69d94fd03c1972383dae133feddee23167ab5f3a807bb3e36827e324308ca
- State: State.READY
- Name: example-processor
- Image: foobar/proc_example/example-processor:cb9e3dceca3f4270106c3aa9fe2bcf7351fd9b8b521f748ed79fb6782022ce38
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
? Select the processor: ['06f6...08ca: example-processor [State.READY] local:///Users/foobar/Desktop/repositories/sim-aas-middleware/examples/adapters@cb9e3d...'
]
Undeploying processor 06f69d94fd03c1972383dae133feddee23167ab5f3a807bb3e36827e324308ca...Done
```

For a list of all arguments, use:
```shell
simaas-cli rti proc --help
```
