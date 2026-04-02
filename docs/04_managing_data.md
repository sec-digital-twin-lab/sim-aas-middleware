# Managing Data

All data in Sim-aaS flows through the Data Object Repository (DOR). This guide covers how to add, search, download, tag, and control access to data objects using the CLI.

All commands in this guide assume a running Sim-aaS node. See [Getting Started](01_getting_started.md) for setup instructions.

## Adding Data Objects

Add a file to the DOR:

```bash
simaas-cli dor add --data-type JSONObject --data-format json my_data.json
```

The command prompts for the node address, keystore, password, and co-creators. On success, it returns the full metadata including the `obj_id` and `c_hash` (content hash).

### Options

| Option | Description |
|--------|-------------|
| `--data-type TYPE` | Logical data type (e.g., `JSONObject`, `Image`, `CSV`). Must match what processors expect. |
| `--data-format FORMAT` | File format (e.g., `json`, `png`, `csv`). Must match what processors expect. |
| `--restrict-access` | Make the data object accessible only to the owner and explicitly granted identities. |
| `--encrypt-content` | Encrypt the content using the keystore's content keys. Only identities with the decryption key can read it. |
| `--assume-creator` | Skip the co-creator selection prompt and record your identity as the sole creator. |

### What Happens on Add

When you add a data object, the DOR:

1. Computes a SHA-256 content hash of the file
2. Stores the file in its content store (read-only)
3. Creates a metadata record with the type, format, owner, content hash, timestamp, and other attributes
4. Returns the complete metadata including the assigned `obj_id`

The content hash means identical files always get the same `c_hash`, enabling deduplication and content-addressable provenance.

## Searching for Data Objects

Search by pattern:

```bash
simaas-cli dor search "my_data"
```

Filter by type or format:

```bash
simaas-cli dor search --data-type JSONObject --data-format json
```

List only your own data objects:

```bash
simaas-cli dor search --own
```

Add `--json` for machine-readable output.

## Getting Metadata

Retrieve the full metadata for a data object:

```bash
simaas-cli dor meta --obj-id <obj-id>
```

This returns the object's type, format, owner, content hash, creation details, access list, tags, license, custodian node, and provenance recipe (if it was produced by a processor).

## Downloading Content

Download one or more data objects:

```bash
simaas-cli dor download <obj-id>
simaas-cli dor download <obj-id-1> <obj-id-2> <obj-id-3>
```

You'll be prompted for a destination directory. Files are saved as `<obj-id>.<format>`.

Downloading requires access: either the data object is unrestricted, or your identity is on the access list.

## Removing Data Objects

Remove data objects you own:

```bash
simaas-cli dor remove <obj-id>
simaas-cli dor remove <obj-id-1> <obj-id-2>    # remove multiple
```

Add `--confirm` to skip the confirmation prompt.

Only the owner can remove a data object.

## Tags

Tags are key-value metadata attached to data objects. They're useful for organizing, filtering, and annotating data.

Add or update tags:

```bash
simaas-cli dor tag --obj-id <obj-id> experiment=run_42 status=validated
```

Remove tags by key:

```bash
simaas-cli dor untag --obj-id <obj-id> status
```

Tag values can be strings, numbers, booleans, lists, or dicts. Only the owner can modify tags.

## Access Control

By default, data objects are unrestricted: anyone can read them. To create a restricted data object:

```bash
simaas-cli dor add --data-type JSONObject --data-format json --restrict-access sensitive_data.json
```

### Viewing Access

```bash
simaas-cli dor access show --obj-id <obj-id>
```

### Granting Access

Grant access to one or more data objects for an identity:

```bash
simaas-cli dor access grant <obj-id> --iid <identity-id>
simaas-cli dor access grant <obj-id-1> <obj-id-2> --iid <identity-id>
```

### Revoking Access

Revoke access from one or more identities:

```bash
simaas-cli dor access revoke --obj-id <obj-id> <identity-id-1> <identity-id-2>
```

Only the owner can grant or revoke access.

## Content Encryption

Data objects can be encrypted at rest:

```bash
simaas-cli dor add --data-type JSONObject --data-format json --encrypt-content secret_data.json
```

Encrypted data objects use the keystore's content keys. Only identities with the corresponding decryption key can read the content. This is orthogonal to access control: a data object can be both access-restricted and content-encrypted.

## Licensing

Data objects carry Creative Commons-style license flags:

| Flag | Meaning |
|------|---------|
| `by` | Attribution required |
| `sa` | Share-alike: derivatives must use the same license |
| `nc` | Non-commercial use only |
| `nd` | No derivatives allowed |

These are metadata. The middleware does not enforce license terms, but they are included in the data object metadata and propagated through provenance chains.

## Data Types and Formats

The middleware does not interpret `data_type` or `data_format` values. They are opaque strings used for matching inputs to processor descriptors. Choose consistent conventions for your project:

| Use case | Suggested data_type | Suggested data_format |
|----------|--------------------|-----------------------|
| JSON data | `JSONObject` | `json` |
| CSV tables | `CSV` | `csv` |
| Images | `Image` | `png`, `jpg`, `tiff` |
| Binary data | `Binary` | `bin` |
| Processor images | `DockerImage` | `tar` |

The only constraint: what you specify when adding a data object must match what the consuming processor declares in its descriptor (or match a wildcard pattern if the processor uses one).
