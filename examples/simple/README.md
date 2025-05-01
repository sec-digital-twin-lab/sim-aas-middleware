# Simple Example: Basic Input/Output Processor

This example demonstrates how a simple processor can be used to process two input values 
and produce an output, primarily used for testing purposes.

The example consists of one processor:

- **`ProcessorABC`** - a processor that takes two inputs, `a` and `b`, and produces an 
output `c`.

## Objective

Compute the sum of two numbers `a` and `b`, or use a predefined value if the environment 
variable `SECRET_ABC_KEY` is set. The result is written to a file `c` in the working 
directory.

This setup demonstrates how to:
- Read input data from files.
- Optionally use an environment variable to influence computation.
- Write the output to a well-defined file.
- Report progress and job status via a listener.

## Processor

- **`ProcessorABC`**:
  - Accepts inputs: `a` (numeric value), `b` (numeric value).
  - Computes `c = a + b` unless the environment variable `SECRET_ABC_KEY` is defined.
  - Writes the result to a file `c` in the working directory.
  - Reports progress through the `listener` and sends messages at key steps.
  - Supports cancellation during computation.

## Example Parameters

The `parameters` input used by `ProcessorABC` might look like:

```json
{
  "a": 5,
  "b": 10
}
