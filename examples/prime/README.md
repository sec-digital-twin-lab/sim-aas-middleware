# Factorisation Example: Dynamic Child Job Submission

This example demonstrates how a processor can dynamically **create and manage child jobs on-the-fly** 
using the RTI (Runtime Interface) provided by the Sim-aaS Middleware.

The example consists of two processors:

- **`ProcessorFactorisation`** – an orchestrator that divides a factorisation task into sub-jobs.
- **`ProcessorFactorSearch`** – a worker that computes factors of a number over a specific sub-range.

## Objective

Factor a given number `N` by breaking the task into `K` sub-jobs. Each sub-job searches for factors 
in a subset of the range `[2, N)`.

This setup demonstrates how to:
- Dynamically discover available processors via the RTI.
- Submit child jobs at runtime.
- Monitor job status and retrieve output data.
- Aggregate results from distributed computation.

## Processors

- `ProcessorFactorisation`:
  - Accepts inputs: `N` (number to factor), `num_sub_jobs` (number of partitions).
  - Submits `num_sub_jobs` child jobs to `ProcessorFactorSearch`.
  - Waits for their completion, gathers the factor lists, and writes the final result.

- `ProcessorFactorSearch`:
  - Accepts a specific range (`start`, `end`) and the number `N`.
  - Computes and returns all factors of `N` in that range.

## Example Parameters

The `parameters` input used by `ProcessorFactorisation` might look like:

```json
{
  "N": 123456,
  "num_sub_jobs": 4
}
```