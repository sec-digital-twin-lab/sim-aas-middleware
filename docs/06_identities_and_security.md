# Identities and Security

Sim-aaS uses cryptographic identities for authentication and authorization. There are no usernames, passwords, or sessions. Every API request is signed with a private key and verified against the corresponding public key.

## The Security Model

### How It Works

Each participant has a **keystore**, an encrypted file containing cryptographic keys:

- **Master key** (RSA): protects all other keys at rest; optionally password-protected
- **Signing key** (EC/ECDSA): signs API requests and identity tokens
- **Encryption key** (RSA): encrypts data object content
- **Curve key** (derived from signing key): encrypts P2P communication via CurveZMQ

From these keys, a public **identity** is derived: a record containing the identity ID, name, email, and public keys. Identities are shared with other nodes; private keys never leave the keystore.

### Request Authentication

Every REST API request includes two custom headers:

- `saasauth-iid`: the caller's identity ID
- `saasauth-signature`: a signature over `METHOD:URL` + the canonical JSON body

The receiving node looks up the identity's public key and verifies the signature. If verification fails, the request is rejected. This means:

- Every request is independently verifiable
- No bearer tokens that can be stolen and replayed
- No central auth server that can be compromised
- No session state to manage

### Authorization

Authorization is enforced at the API level through declarative rules:

- **Data object ownership**: only the owner can delete, modify tags, grant/revoke access, or transfer ownership
- **Access control**: restricted data objects can only be read by identities on the access list
- **Job ownership**: only the job submitter (or node owner) can view status, cancel, or purge
- **Deployment control**: in strict mode, only the node owner can deploy/undeploy processors

### Design Philosophy

The middleware deliberately handles only cryptographic identity. User-facing authentication (OAuth2, LDAP, SSO) belongs at the application layer (a web application or API gateway in front of the middleware). This separation keeps the middleware focused and avoids coupling it to any particular auth provider.

## Managing Identities

### Creating an Identity

```bash
simaas-cli identity create
```

Prompts for name, email, and password. Creates a keystore file in `~/.keystore/`.

The password encrypts the master key. All other keys are encrypted with the master key. If you lose the password, the keystore is unrecoverable.

### Listing Identities

```bash
simaas-cli identity list
```

Lists all keystores in your keystore directory. Add `--json` for machine-readable output.

### Viewing Keystore Details

```bash
simaas-cli identity show
```

Shows the full keystore contents: identity ID, name, email, public keys, stored credentials, and key fingerprints.

### Updating Identity

```bash
simaas-cli identity update --name "New Name" --email "new@example.com"
```

Updates the identity profile. The identity is re-signed after any update.

### Removing an Identity

```bash
simaas-cli identity remove
```

Permanently deletes the keystore file. Add `--confirm` to skip the confirmation prompt.

## Publishing and Discovering Identities

For other nodes to verify your requests, they need your public identity.

### Publishing

Push your identity to a node:

```bash
simaas-cli identity publish --address <host:port>
```

The node stores your identity and shares it with its peers.

### Discovering

List all identities known to a node:

```bash
simaas-cli identity discover --address <host:port>
```

Add `--json` for machine-readable output.

## Managing Credentials

Keystores can store credentials used by the middleware for specific operations.

### SSH Credentials

Used for SSH tunneling (e.g., connecting to remote nodes through bastion hosts):

```bash
simaas-cli identity credentials add ssh \
    --name my-server \
    --host bastion.example.com \
    --login ubuntu \
    --key ~/.ssh/id_rsa
```

Add `--passphrase` if the key is passphrase-protected.

Test the connection:

```bash
simaas-cli identity credentials test ssh --name my-server
```

### GitHub Credentials

Used for building PDIs from private GitHub repositories:

```bash
simaas-cli identity credentials add github \
    --url https://github.com/org/repo \
    --login my-username \
    --personal-access-token ghp_xxx
```

Test the connection:

```bash
simaas-cli identity credentials test github --url https://github.com/org/repo
```

You can also set GitHub credentials via environment variables (`GITHUB_USERNAME`, `GITHUB_TOKEN`) instead of storing them in the keystore.

### Listing and Removing Credentials

```bash
simaas-cli identity credentials list
simaas-cli identity credentials remove --credential ssh:my-server
```

## Keystore File Format

Keystores are stored as encrypted JSON files at `~/.keystore/<identity-id>.json`. The structure:

- All keys except the master key are encrypted with the master key
- The master key is encrypted with the user's password (using PBKDF2 key derivation)
- The identity (public component) is recalculated and re-signed on every keystore modification
- Thread-safe access via mutex

The keystore is fully self-contained. You can back it up by copying the JSON file. Restoring it on another machine gives you the same identity with all keys and credentials intact.

## Identity Properties

Each identity has:

| Property | Description |
|----------|-------------|
| `id` | Unique identifier (derived from the public key) |
| `name` | Human-readable name |
| `email` | Email address |
| `signing_key` | EC public key for signature verification |
| `encryption_key` | RSA public key for encrypting data |
| `curve_key` | ZMQ Curve public key for encrypted P2P transport |
| `nonce` | Monotonically increasing counter, incremented on each identity update |
| `signature` | Self-signature for integrity verification |

The nonce prevents replay attacks with stale identity versions. The self-signature ensures the identity hasn't been tampered with.
