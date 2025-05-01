# Create Identity
> *If you are using the Sim-aaS Middleware for the first time, you need to create an identity.*

Identities are used across the Sim-aaS system for authentication/authorisation purposes as well 
as for managing ownership and access rights to data objects. An identity is required to operate 
Sim-aaS node instances or to interact with remote instances.

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

After creating identities, the user can list all the keystores found in the keystore path using:
```shell
simaas-cli identity list

Found 1 keystores in '/home/foo.bar/.keystore':
NAME     EMAIL              KEYSTORE/IDENTITY ID
----     -----              --------------------
foo bar  foo.bar@email.com  i6vmw1hffcsf5pg6dlc4ofxl1s95czqs6uuqg8mf9hz32qdei4b8gmwu4eivtm3t
```

The `--keystore` flag can be provided to the command above if it is not found in the default path.

## Credentials
The keystore can also be used to store and associate credentials with the identity. 
These credentials can be used for deploying processors and running jobs.
For example, GitHub for cloning from private repositories or SSH for executing remote commands.
More information about deploying processors and running jobs can be found in the sections below.

Credentials (SSH or GitHub) can be added by following the prompts using:
```shell
simaas-cli identity credentials add ssh

# OR

simaas-cli identity credentials add github
```

For a list of all commands concerning identities, use:
```shell
simaas-cli identity --help
```
