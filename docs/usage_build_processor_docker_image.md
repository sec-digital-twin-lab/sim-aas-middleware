# Building a Processor Docker Image (PDI)
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
Done cloning https://github.com/sec-digital-twin-lab/sim-aas-middleware.
Building image 'sec-digital-twin-lab/sim-aas-middleware/example-processor:33d795ecbd53eae4c4f9ef3c9d7a8e90821a0053'. This may take a while...
Done building image 'sec-digital-twin-lab/sim-aas-middleware/example-processor:33d795ecbd53eae4c4f9ef3c9d7a8e90821a0053'.
Done uploading PDI to DOR -> object id: d899526f24ea732482f040cdbda4ebf72cab0e46c936a6a729e476565bcc7493
```

> If the repository is not public it is necessary to specify the necessary Github credentials 
> `GITHUB_USERNAME` and `GITHUB_TOKEN` either in a `.env` file or directly as environment 
> variables.

> Processor Docker Images depend on the `sim-aas-middleware` repository. At the time of writing,
> this repository is private. Ensure the GitHub credentials used have **full access** to the the
> `sim-aas-middleware` repository.
