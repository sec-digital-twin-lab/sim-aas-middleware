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

## Designing Data Contracts for Multi-Adapter Systems

When building a system of multiple adapters that chain together, the `data_type` and `data_format` fields become your interoperability contract. Careful upfront design prevents integration headaches later.

### Namespaced Data Types

Use a project-level prefix to avoid collisions across teams or projects:

| Use case | data_type | data_format |
|----------|-----------|-------------|
| Building footprints | `MyProject.GeoVectorData` | `geojson` |
| Raster climate output | `MyProject.ClimateVariables` | `hdf5` |
| Simulation run package | `MyProject.RunPackage` | `tar.gz` |
| Energy demand results | `MyProject.EnergyDemand` | `json` |

When one adapter produces `MyProject.RunPackage` / `tar.gz` and another declares it as an input, the middleware enforces the match at submission time.

### Composite and Archive Formats

Many real-world adapters produce or consume multi-file outputs — a simulation run package with config files, NetCDF drivers, and metadata, or a results archive with multiple GeoTIFFs. Common patterns:

- **tar.gz / zip archives**: Bundle multiple files into a single data object. The producing adapter packs them; the consuming adapter unpacks and knows the internal layout by convention.
- **HDF5**: Store multi-dimensional arrays with metadata (units, timestamps, bounding boxes, coordinate reference systems) in a single file. Useful for spatio-temporal simulation output.

The middleware treats these as opaque blobs — it stores and transfers them without interpretation. The contract between producer and consumer is the internal structure, which should be documented alongside your descriptors.

### Schema Validation

For JSON data objects, use the `data_schema` field in the descriptor to enforce structure at submission time. The middleware validates inputs against the schema before the processor runs, and validates outputs after:

```json
{
  "name": "parameters",
  "data_type": "MyProject.SimParameters",
  "data_format": "json",
  "data_schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string"},
      "bbox": {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 4,
        "maxItems": 4
      },
      "resolution_m": {"type": "number", "minimum": 1}
    },
    "required": ["name", "bbox", "resolution_m"]
  }
}
```

For non-JSON formats (GeoTIFF, HDF5, archives), schema validation is not built in. Adapters should validate these in their `run()` method and raise `ProcessorRuntimeError` with a clear message on mismatch.

### Documenting Your Contracts

For any multi-adapter system, maintain a registry of your data types — what each type represents, its internal structure, and which adapters produce and consume it. This is external to the middleware but essential for anyone building or extending the system.
