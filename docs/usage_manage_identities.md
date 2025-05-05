# Manage Identities
> *If you are using the Sim-aaS Middleware for the first time, you need to create an identity.*

> *If you are not yet familiar with the concepts of keystores and identities, you can read up
> on it [here](./concept_keystore_and_identities.md).

For a list of all commands concerning identities, use:
```shell
simaas-cli identity --help
```

## Create new keystore/identity
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
```
Example dialogue:
```
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

## List keystores/identities
After creating identities, the user can list all the keystores found in the keystore path using:
```shell
simaas-cli identity list
```
Example dialogue:
```
Found 1 keystores in '/home/foo.bar/.keystore':
NAME     EMAIL              KEYSTORE/IDENTITY ID
----     -----              --------------------
foo bar  foo.bar@email.com  i6vmw1hffcsf5pg6dlc4ofxl1s95czqs6uuqg8mf9hz32qdei4b8gmwu4eivtm3t
```

The `--keystore` flag can be provided to the command above if it is not found in the default path.
```shell
simaas-cli --keystore $KEYSTORE_PATH identity list
```

## Remove existing keystores/identities
Removes a keystore from the local keystore directory. The user will be asked to confirm before deletion.
```shell
simaas-cli identity remove
```

## Show details
Displays the full profile and cryptographic identity of a specific keystore.
```shell
simaas-cli identity show
```

## Update profile
Updates fields such as name or email in the identity profile and re-signs the keystore.
```shell
simaas-cli identity update
```

## Retrieve a list of all identities known to a node
Contacts a remote node and retrieves its list of published identities.
```shell
simaas-cli identity discover
```
Example dialogue:
```
? Enter address of node for discovery: 192.168.50.126:5001
Discovered 1 identities:
NAME  EMAIL          IDENTITY ID
----  -----          -----------
test  test@test.com  hm9jatxdtcmr6vo3qrcye5lut7f4j8aeunaue6i3ehruajedv4cbk2qa1tzin1cf
```

## Publish an identity to a node
Pushes your identity to a remote node, making it discoverable by others. *Note: only the
information of the identity portion of the keystore is published to the node.*
```shell
simaas-cli identity publish
```
Example dialogue:
```
? Select the keystore: foo bar/foo.bar@email.com/ra9zle3dj1sfsua5bmxi6c66oz8yk93dbijigfefgzm0bhvkv251fkxsw93hk64r
? Enter password: ****
? Enter the target node's REST address 192.168.50.126:5001
Published identity of keystore ra9zle3dj1sfsua5bmxi6c66oz8yk93dbijigfefgzm0bhvkv251fkxsw93hk64r
```

## Manage Credentials
The keystore can also be used to store and associate credentials with the identity. These 
credentials can be used for deploying processors and running jobs. For example, GitHub for 
cloning from private repositories or SSH for executing remote commands. More information about 
deploying processors and running jobs can be found in the sections below.

Credentials (SSH or GitHub) can be added by following the prompts using:
```shell
simaas-cli identity credentials add ssh
```
```shell
simaas-cli identity credentials add github
```

For a list of all commands concerning credentials, use:
```shell
simaas-cli identity credentials --help
```

### Example: Add and test GitHub Credentials
This examples shows how to add and test GitHub credentials. First add the credentials:
```shell
simaas-cli identity credentials add github
```

The dialogue will ask information about the GitHub username and the personal access token:
```
? Select the keystore: test/test@test.com/hm9jatxdtcmr6vo3qrcye5lut7f4j8aeunaue6i3ehruajedv4cbk2qa1tzin1cf
? Enter password: ****
? Enter repository URL: https://github.com/sec-digital-twin-lab/sim-aas-middleware
? Enter login: <USERNAME>
? Enter personal access token: <TOKEN>
Credential successfully created.
```

To see if the credentials are working, you can test them:
```shell
simaas-cli identity credentials test github
```

In the dialogue, select the credentials you want to test:
```
? Select the keystore: test/test@test.com/hm9jatxdtcmr6vo3qrcye5lut7f4j8aeunaue6i3ehruajedv4cbk2qa1tzin1cf
? Enter password: ****
? Select repository URL: https://github.com/sec-digital-twin-lab/sim-aas-middleware
repo_name: sec-digital-twin-lab/sim-aas-middleware
Github credentials test successful.
```