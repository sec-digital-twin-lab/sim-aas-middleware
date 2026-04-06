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
simaas-cli dor --address 192.168.50.126:5001 access revoke

? Select the keystore: test/test@email.com/ls1v4ohq59selxhvi9mleak9lgmq6xz2irhxzje46j7nfbee2o6ssac61kg19shq
? Enter password: ****
? Select data object: 9f5b...a85c [JSONObject:json] name=data_object_a.json
? Select the identities whose access should be removed: ['gosigg6nq3n89528w7lm5vqt07ookb1ort30klil8xzhz9mkff5aooanj5awsjtk - foo bar <for.bar@email.com>']
Revoking access to data object 9f5b...a85c for identity gosi...sjtk...Done
```
