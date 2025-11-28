# Manage Processor Docker Images (PDIs)
The other core module of a Sim-aaS Node is the Runtime Infrastructure (RTI). It executes
computational jobs using processors that have been deployed on the node. Depending on the
processor, a job will use some input data (provided by a DOR in form of data objects
or parameters in form of a json object) and produce some output data (as data objects that will
be stored on a DOR). Exactly what input is consumed and what output is produced is specified
by the descriptor of the processor. See "Processor" for more information on this topic.

## PDI Meta Information
Before a processor can be deployed, it has to be built first and uploaded to the DOR in form
of a Processor Docker Image (PDI). A PDI is a file that is essentially a docker image with 
additional meta information:
```
| Docker image of processor | Meta Information | PDI_META | Length | 
```

There are two CLI commands that can be used to build a PDI: `build-local` and `build-github` 
depending on whether the source of the processor can be found locally or in a Github repository.
The build commands combine the docker image with PDI meta information and store the resulting
PDI as files which can then be imported into a DOR for further use. 

PDI meta information includes information such as the repository URL, commit id, content hash 
and image name. Note that the build command will try to establish if the processor is part 
of a Git repository. If so, it will generate corresponding meta information. For example,
the following two excerpts show meta information if the processor directory is part of a Git
and not.

Example for PDI meta information for processor part of a Git repository:
```
"proc_path": "examples/simple/abc",
"repository": "https://github.com/sec-digital-twin-lab/sim-aas-middleware",
"commit_id": "e81119ada241cd9047e3e6799c932a73f1f7bddc",
"content_hash": "b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b",
"is_git_repo": true,
"is_dirty": true,
"image_name": "proc-abc:b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b"
```

Example for PDI meta information for processor not part of a Git repository:
```
"proc_path": "Desktop/simple/abc",
"repository": "local://foobar@Desktop/simple/abc",
"commit_id": null,
"content_hash": "b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b",
"is_git_repo": false,
"is_dirty": false,
"image_name": "proc-abc:b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b"
```

Note that commit ids are only provided if the processor is part of a Git repository. 
The `is_dirty` flag also indicates `true` if there are changes from the commit that have
not been committed yet. In general the content hash should be used to determine if two
processors are exactly the same. For processors that are not part of a Git repository, the
`is_dirty` flag is always `false` since there is no commit state.

## Building PDIs from Local Source
The `build-local` command generates a PDI file from an existing local source. Example for 
building a PDI from a local source:
```shell
simaas-cli image build-local --arch linux/amd64 simple/abc ~/Desktop

SHA256 hash of processor contents: b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b
Using PDI file destination at '/Users/foobar/Desktop/proc-abc_b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b.pdi'.
Begin building PDI 'proc-abc:b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b'. This may take a while...
Using existing docker image.
Done exporting docker image.
Done building PDI.
```
> Building a PDI requires installing a version of the Sim-aaS Middleware. The build command
> needs to know where to find it. The location can be either specified by using the 
> `--simaas-repo-path` attribute or by defining an environment variable `SIMAAS_REPO_PATH`
> with the path to the Sim-aaS Middleware.

> In principle, the `SIMAAS_REPO_PATH` may point at another version of the Sim-aaS Middleware
> that is used by the build command itself. However, for consistency it is advised that 
> `SIMAAS_REPO_PATH` should point at the same version of the Sim-aaS Middleware used by the
> build commands.

The build command will calculate a SHA256 hash of the content of the processor (i.e., the hash 
over all the files in all subfolders in processor directory). This content hash will be used as
part of the PDI name. The naming convention for PDIs is `<processor name>:<content hash>`. In 
the example above, the PDI name is `proc-abc:b3cb...1b6b`. 

The build command expects a path to the location of the processor (`simple/abc`) and the 
destination of the PDI file (`~/Desktop`). If a directory is provided as destination, a 
generic filename will be used. The naming convention is `<processor name>_<content hash>.pdi`. 
In the example, the PDI file will thus be generated at 
`/Users/foobar/Desktop/proc-abc_b3cb...1b6b.pdi`. If a valid file path is specified as PDI
destination, the resulting PDI be created at that exact location (e.g., 
`/Users/foobar/Desktop/my_custom_proc_image.pdi`).

Unless the architecture is explicitly specified (`--arch linux/amd64`), the local architecture 
will be used by default (e.g., `darwin/arm64` on MacOS). For cross-platform compatibility it 
can be useful to always build using `linux/amd64`. These images will be able to run on MacOS
using Docker RTIs as well on AWS Batch using AWS RTI.

Docker images are only generated if they don't already exist. The build command will skip 
building the docker image (which can be time-consuming) if possible. It is possible to force
building a new docker image by using the `--force-build` flag. 

The `--verbose` flag can be used to display the PDI meta information:
```
Appending PDI meta information: {
  "proc_descriptor": {
    "name": "proc-abc",
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
    ],
    "required_secrets": [
      "SECRET_ABC_KEY"
    ]
  },
  "proc_path": "examples/simple/abc",
  "repository": "https://github.com/sec-digital-twin-lab/sim-aas-middleware",
  "commit_id": "e81119ada241cd9047e3e6799c932a73f1f7bddc",
  "content_hash": "b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b",
  "is_git_repo": true,
  "is_dirty": true,
  "image_name": "proc-abc:b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b"
}
```

## Building PDIs from a Github Source
The `build-github` command works in the same way as the `build-local` command. It performs
the same build operations to generate a PDI file. However, unlike `build-local`,
`build-github` first clones a Github repository to obtain the processor code. Example for 
building a PDI from a Github source:
```shell
simaas-cli image build-github --repository https://github.com/sec-digital-twin-lab/sim-aas-middleware --commit-id e81119ada241cd9047e3e6799c932a73f1f7bddc --proc-path examples/simple/abc --arch linux/amd64 --verbose /Users/foobar/Desktop

Using repository at https://github.com/sec-digital-twin-lab/sim-aas-middleware with commit id e81119ada241cd9047e3e6799c932a73f1f7bddc.
Using processor path 'examples/simple/abc'.
Not using any GitHub credentials.
Done cloning https://github.com/sec-digital-twin-lab/sim-aas-middleware.
SHA256 hash of processor contents: b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da
Using PDI file destination at '/Users/foobar/Desktop/proc-abc_b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da.pdi'.
Begin building PDI 'proc-abc:b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da'. This may take a while...
Done building docker image (forced build: NO).
Done exporting docker image.
Appending PDI meta information: {
  "proc_descriptor": {
    "name": "proc-abc",
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
    ],
    "required_secrets": [
      "SECRET_ABC_KEY"
    ]
  },
  "proc_path": "examples/simple/abc",
  "repository": "https://github.com/sec-digital-twin-lab/sim-aas-middleware",
  "commit_id": "e81119ada241cd9047e3e6799c932a73f1f7bddc",
  "content_hash": "b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da",
  "is_git_repo": true,
  "is_dirty": false,
  "image_name": "proc-abc:b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da"
}
Done building PDI.
```

> If the repository is not public it is necessary to specify the necessary Github credentials 
> `GITHUB_USERNAME` and `GITHUB_TOKEN` either in a `.env` file or directly as environment 
> variables.


## Importing PDIs to a DOR
After a PDI has been built, it can be imported to a DOR. For example:
```shell
simaas-cli image import --address 192.168.50.119:5001 ~/Desktop/proc-abc_b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da.pdi

? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
Importing PDI at /Users/foobar/Desktop/proc-abc_b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da.pdi' done -> object id: 59b64d781cfcd74ce5f31af11d1442d9e5109e1cf8f52b9e139f592b188e1f54
```

Once it is imported, the processor can be deployed and used to process jobs.


## Exporting PDIs from a DOR
PDIs that are stored in the DOR can also be exported as PDI files. For example:
```shell
simaas-cli image export ~/Desktop

? Enter the target node's REST address 192.168.50.119:5001
? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
? Select PDI for export 92e1...9163 [ProcessorDockerImage:tar] proc_descriptor=... proc_path=examples/simple/abc repository=https://github.com/sec-digital-twin-
lab/sim-aas-middleware commit_id=2046cb1743186fd5ad4ade118c62cb36d2324253 content_hash=b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b is_git_r
epo=1.0 is_dirty=1.0 image_name=proc-abc:b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b
Exporting PDI (object id: 92e1f7b9596234712527607de0402bdb371df01c54340a1eb48c627700d69163) done -> path: /Users/foobar/Desktop/proc-abc_b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b.pdi.
```
