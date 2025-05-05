# Keystores and Identities
A **keystore** is a structured JSON file that stores a user's identity and essential cryptographic
material. It includes a profile, public/private key pairs, and credentials for encryption, 
signing, and interfacing with external services (e.g., GitHub, SSH). These assets are encrypted 
and authenticated, ensuring both confidentiality and integrity. **Identities** are the public
key component of the keystore, used across the Sim-aaS system for authentication/authorisation 
purposes as well as for managing ownership and access rights to data objects. An identity is 
required to operate Sim-aaS node instances or to interact with remote instances.

### Core Responsibilities 
- Stores cryptographic assets (signing/encryption keys, master key, etc.)
- Secures credentials for SSH and GitHub access 
- Signs and verifies data 
- Encrypts and decrypts content 
- Maintains and verifies a digital identity derived from stored keys 
- Handles persistence and synchronization of content to disk

### Contents of a Keystore
Each keystore contains the following core assets:
- `master-key`: An RSA key pair used to protect other assets via encryption.
- `signing-key`: An EC key pair used to sign keystore content and identity tokens.
- `encryption-key`: An RSA key pair used to encrypt and decrypt data payloads.
- `content-keys`: A `ContentKeysAsset` for managing symmetric encryption keys.
- `ssh-credentials`: An `SSHCredentialsAsset` storing SSH key-based credentials.
- `github-credentials`: A `GithubCredentialsAsset` storing GitHub-related tokens.

All assets except the master key are encrypted with the master key. The master key 
itself is protected using a user-provided password.

### Identity
The keystore maintains a cryptographic identity that includes:
- A unique ID (`iid`)
- A profile (name and email)
- Public keys (signing, encryption, and Curve-compatible)
- A nonce to track updates
- A digital signature for integrity verification

The identity is recalculated and re-signed whenever the keystore is updated.

### Persistence
The keystore can be:
- Created from scratch (`Keystore.new`)
- Loaded from a JSON file (`Keystore.from_file`)
- Loaded from a validated model object (`Keystore.from_content`)
It ensures the integrity of its data by verifying the signature on load and re-signing 
on every update (`sync()` method).

### Encryption & Signing Capabilities
The keystore supports the following methods for cryptographic operations:
- `encrypt(content: bytes)`: Encrypts content using the encryption key
- `decrypt(content: bytes)`: Decrypts content using the encryption key
- `sign(message: bytes)`: Signs data using the signing key
- `verify(message: bytes, signature: str)`: Verifies a signature

### ZeroMQ Curve Support
To support secure ZeroMQ communication:
- A Curve-compatible secret key (`curve_secret_key`)
- A corresponding public key (`curve_public_key`)
are derived from the signing key.

### Thread-Safety
Access to internal state is guarded by a mutex (`Lock`) to support safe 
multi-threaded usage.