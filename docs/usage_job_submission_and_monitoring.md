# Submit Job and Check Status
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
