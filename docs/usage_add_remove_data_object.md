# Adding and Removing a Data Object
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

## Granting and Revoking Access to Data Objects 
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
