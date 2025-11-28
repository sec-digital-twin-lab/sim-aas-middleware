# Manage Processor Docker Images (PDIs)
The Runtime Infrastructure (RTI) is a core module of a Sim-aaS Node that executes
computational jobs using processors deployed on the node. A processor consumes input data
(provided by a DOR as data objects or as parameters in a JSON object) and produces output
data (as data objects stored in a DOR). The processor descriptor specifies exactly what
input is consumed and what output is produced. See "Processor" for more information on
this topic. Before a processor can be deployed, it must first be built and uploaded to
the DOR in the form of a Processor Docker Image (PDI). 

## PDI Meta Information
A PDI is a file that combines a Docker image with additional meta information:
```
┌─────────────────────────────────┬──────────────────┬──────────┬─────────┐
│   Docker Image (TAR format)     │ Meta Information │ PDI_META │ Length  │
│         (variable size)         │  (JSON, varies)  │ (8 bytes)│(4 bytes)│
└─────────────────────────────────┴──────────────────┴──────────┴─────────┘
```
The Docker image contains the processor and its dependencies, followed by meta information
in JSON format. The marker `PDI_META` (8 bytes) identifies the end of the meta information,
and the length field (4-byte big-endian integer) specifies the size of the meta information
section.

Two CLI commands are available to build PDIs: `build-local` for processors available locally,
and `build-github` for processors in a GitHub repository. Both commands combine the Docker
image with PDI meta information and store the resulting PDI as a file that can be imported
into a DOR for further use.

PDI meta information includes the repository URL, commit ID, content hash, and image name.
The build command automatically detects whether the processor is part of a Git repository
and generates the corresponding meta information. The following two examples show the
difference between processors that are part of a Git repository and those that are not.

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

Note that commit IDs are only provided when the processor is part of a Git repository.
The `is_dirty` flag is set to `true` when there are uncommitted changes in the repository.
The content hash should be used to determine if two processors are identical, as it reflects
the actual file contents regardless of Git state. For processors that are not part of a Git
repository, the `is_dirty` flag is always `false` since there is no commit state to compare against.

## Building PDIs from Local Source
The `build-local` command generates a PDI file from a local processor directory. Example:
```shell
simaas-cli image build-local --arch linux/amd64 simple/abc ~/Desktop

SHA256 hash of processor contents: b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b
Using PDI file destination at '/Users/foobar/Desktop/proc-abc_b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b.pdi'.
Begin building PDI 'proc-abc:b3cb3eac16889a6b3881cb3086dc0ea652e1888e55c7ae18f3a1293caa681b6b'. This may take a while...
Using existing docker image.
Done exporting docker image.
Done building PDI.
```
> Building a PDI requires the Sim-aaS Middleware. Specify its location using the
> `--simaas-repo-path` flag or by setting the `SIMAAS_REPO_PATH` environment variable.
> For consistency, use the same version of the Sim-aaS Middleware that runs the build command.

The build command calculates a SHA256 hash of the processor contents (all files in the
processor directory and its subdirectories). This content hash is used as part of the PDI name,
following the convention `<processor name>:<content hash>`. In the example above, the PDI name
is `proc-abc:b3cb...1b6b`.

The command requires two arguments: the processor path (`simple/abc`) and the PDI destination
(`~/Desktop`). When a directory is provided as the destination, a filename is automatically
generated using the convention `<processor name>_<content hash>.pdi`. In the example, the PDI
file is created at `/Users/foobar/Desktop/proc-abc_b3cb...1b6b.pdi`. Alternatively, specify
a complete file path to control the exact filename (e.g., `/Users/foobar/Desktop/my_custom_proc_image.pdi`).

The build command uses the local architecture by default (e.g., `darwin/arm64` on macOS).
For cross-platform compatibility, specify `--arch linux/amd64` to build images that run
on both macOS Docker RTIs and AWS Batch RTIs.

By default, the build command reuses existing Docker images to save time. If a Docker image
with the same name already exists, the build step is skipped. Use the `--force-build` flag
to rebuild the Docker image even if one already exists. 

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

## Building PDIs from a GitHub Source
The `build-github` command generates a PDI file from a GitHub repository. It clones the
repository, checks out a specific commit, and then performs the same build operations as
`build-local`. Example:
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

> For private repositories, provide GitHub credentials via the `GITHUB_USERNAME` and
> `GITHUB_TOKEN` environment variables (or in a `.env` file), or use the `--git-username`
> and `--git-token` command-line flags.


## Importing PDIs to a DOR
Once a PDI has been built, import it to a DOR using the `import` command. Example:
```shell
simaas-cli image import --address 192.168.50.119:5001 ~/Desktop/proc-abc_b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da.pdi

? Select the keystore: test/test@test.com/ztusqwpk0ht3geq2ut9g9cpxpvj7gxjq64eme2mk3eakz4gear9mtquoo5kt1bqw
? Enter password: ****
Importing PDI at /Users/foobar/Desktop/proc-abc_b74a64c3f7492578fcb46c2df533e446900bb555deb6de6651db43016ee835da.pdi' done -> object id: 59b64d781cfcd74ce5f31af11d1442d9e5109e1cf8f52b9e139f592b188e1f54
```

The PDI is stored as a data object in the DOR with the type `ProcessorDockerImage`. The object
ID returned by the import command can be used to reference this PDI when deploying the processor
or exporting it later.


## Exporting PDIs from a DOR
Export PDIs stored in a DOR back to PDI files using the `export` command. The command
prompts you to select from available PDIs if no object ID is specified. Example:
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

For non-interactive use, specify the object ID directly using the `--obj-id` flag to skip
the selection prompt.
