# Processor Descriptor (`descriptor.json`)
Each processor (or processor adapter) must include a `descriptor.json` file that formally 
defines its **interface contract** with the RTI. This file specifies:
- The **name** of the processor,
- Its **input** data objects (consumed when executing a job),
- Its **output** data objects (produced when the job finishes),
- Any **required secrets** needed that need to be provided to the processor at runtime by the RTI.

This descriptor ensures that processors are self-describing, allowing automated systems to
verify and orchestrate jobs without hardcoded assumptions.

## Descriptor Structure
The descriptor must be a valid JSON file with the following top-level fields:

```json
{
  "name": "example-processor",
  "input": [ /* list of input data objects */ ],
  "output": [ /* list of output data objects */ ],
  "required_secrets": [ /* list of required secrets */ ]
}
```

Each item in the input and output lists represents a data object interface and follows
this structure:
```json
{
  "name": "example_input",
  "data_type": "JSONObject",
  "data_format": "json",
  "data_schema": { /* optional JSON schema */ },
  "optional": false
}
```

## Input/Output Item Fields
| Field         | Type   | Required | Default | Description                                                                                                   |
|---------------|--------|----------|---------|---------------------------------------------------------------------------------------------------------------|
| name	         | string | yes      |         | Name of the data object. Used as filename during execution.                                                   |
| data_type     | string | yes      |         | Semantic type of the data (e.g., JSONObject, GeoTIFF, etc.).                                                  |
| data_format   | string | yes      |         | File format of the data (e.g., json, tiff, csv).                                                              |
| data_schema   | object | no       | null    | Only used if data_type="JSONObject" and data_format="json". Enables content validation against a JSON Schema. |
| optional      | bool   | no       | false   | When `true`, tasks may omit this data object without causing a validation error. For inputs, the task can be submitted without providing this data object. For outputs, the processor may skip producing it without the job being marked as failed. |

> It's important to understand that the Sim-aaS Middleware does not actually interpret the
> contents of data objects. The fields `data_type` and `data_format` are merely used to determine
> if a given data objects stored in the DOR can be used as input for a processor or not. This
> is simply done by comparing the data type/format information in the meta data of a data object
> with the requirements specified in the descriptor.json of a processor. Both, data type and
> format have to match.

## required_secrets
Some processors require access to API keys, credentials, or tokens at runtime. These are 
provided to a processor at runtime by the RTI. In order for the RTI to know that it has to
provide secrets to a processors, the processor's `descriptor.json` file needs to specify them:
```
"required_secrets": ["API_KEY", "MODEL_AUTH_TOKEN"]
```

## Optional Data Objects
By default, all inputs and outputs are required. Setting `"optional": true` on a data object
makes it optional:

- **Optional inputs**: The task can be submitted without providing this data object. The
  processor must handle its absence (e.g., by checking if the file exists in the working
  directory).
- **Optional outputs**: The processor may skip producing this data object. The job will not
  be marked as failed if an optional output is missing.

Validation is performed at task submission time: missing required inputs or unknown input
names are rejected before the container is started. After execution, the job runner verifies
that all required (non-optional) outputs were produced.

See the [defg example](/examples/simple/defg/) for a processor with all-optional inputs and
outputs.

## Example
Here is an example of a `descriptor.json` file:
```json
{
  "name": "proc-abc",
  "input": [
    {
      "name": "a",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    },
    {
      "name": "b",
      "data_type": "JSONObject",
      "data_format": "json",
      "data_schema": {
        "type": "object",
        "properties": {
          "v": {"type": "number"}
        },
        "required": ["v"]
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
          "v": {"type": "number"}
        },
        "required": ["v"]
      }
    }
  ],
  "required_secrets": ["SECRET_ABC_KEY"]
}
```

## Naming Conventions
The name of each input or output data object:
- **Must be a valid filename**: avoid slashes, spaces, or special characters.
- **Should** use lowercase letters and underscores (snake_case) by convention.
- **Should NOT** include file extensions (e.g., .json, .csv, etc.).
- **Should NOT** encode type/format in the name. The `data_format` field serves this purpose.

❌ Bad:
- `AHProfile.json`
- `temp-output.csv`

✅ Good:
- `ah_profile`
- `temp_output`
