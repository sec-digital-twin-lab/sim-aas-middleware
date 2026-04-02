# Simple Example: Optional I/O Data Objects

This example demonstrates how a processor can define **optional inputs and outputs** using
the `optional: true` field in `descriptor.json`. This allows processors to define flexible
contracts where some data objects may be absent at runtime.

The example consists of one processor:

- **`ProcessorDEFG`** — a processor with all-optional inputs (`d`, `e`) and all-optional
outputs (`f`, `g`). It conditionally processes only the inputs that are present and produces
only the corresponding outputs.

## Objective

Demonstrate the optional I/O feature:
- If input `d` is provided and output `f` is declared, compute `f = d * 2`.
- If input `e` is provided and output `g` is declared, compute `g = e * 3`.
- Any combination of inputs/outputs can be omitted without causing validation errors.

This setup demonstrates how to:
- Use `"optional": true` in `descriptor.json` for inputs and outputs.
- Check for the presence of optional input files in the working directory.
- Check which outputs are declared in the task before producing them.
- Handle partial I/O gracefully.

## Descriptor

The `descriptor.json` marks all inputs and outputs as optional:

```json
{
  "name": "proc-defg",
  "input": [
    { "name": "d", "data_type": "JSONObject", "data_format": "json", "optional": true },
    { "name": "e", "data_type": "JSONObject", "data_format": "json", "optional": true }
  ],
  "output": [
    { "name": "f", "data_type": "JSONObject", "data_format": "json", "optional": true },
    { "name": "g", "data_type": "JSONObject", "data_format": "json", "optional": true }
  ],
  "required_secrets": []
}
```

## Conditional Logic Pattern

The processor checks for input presence and declared outputs before processing:

```python
# Check which inputs exist in the working directory
has_d = os.path.isfile(os.path.join(wd_path, 'd'))
has_e = os.path.isfile(os.path.join(wd_path, 'e'))

# Check which outputs are declared in the task
declared_outputs = {o.name for o in job.task.output}

# Process d → f only if both input exists and output is declared
if has_d and 'f' in declared_outputs:
    # ... compute and write f
    listener.on_output_available('f')
```

## Running the Example using Python

Test cases with working code can be found in [test_processor_abc.py](../../../simaas/tests/test_processor_abc.py)
(the DEFG tests share the same test infrastructure).

## Related Documentation

- [Processor Descriptors](../../../docs/processor_descriptor.md) — full documentation of the `optional` field and validation behaviour
- [Processor Implementation Guide](../../../docs/processor_implementation.md) — how to implement the `run()` method
