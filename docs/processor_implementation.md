# Processor Implementation Guide
Each processor is implemented in a file named **processor.py**, which defines a class 
inheriting from **simaas.core.processor.ProcessorBase**. This class is executed by the job 
runner during job execution.

The processor is responsible for performing the core computation of a job using the inputs 
provided and writing any outputs to disk. The job runner handles orchestration: preparing the 
environment, managing input/output data, and invoking your processor via its `run()` method.

## Required Interface
A valid processor must define a class that inherits from `ProcessorBase`, implementing the 
following two methods:
- `run(wd_path: str, job: Job, listener: ProgressListener, namespace: Namespace, logger: Logger)`
- `interrupt()`

## `run()` Method
This is the main entry point for execution. It is a blocking call—returning only after the 
processor completes or fails.

### Parameters
- `wd_path: str`: Path to the job working directory. All input files are placed here before 
execution, and all output files must be written here. The runner ensures this directory exists 
and is cleaned up after completion.
- `job: Job`: Contains metadata about the job, including inputs, outputs, and user information.
- `listener: ProgressListener`: Used to communicate progress, status messages, and output 
availability to the job runner. 
- `namespace: Namespace`: Provides contextual identity and P2P communication capabilities (e.g., 
access to the custodian or other nodes).
- `logger: Logger`: A structured logging interface. Avoid using `print()`; use this logger for 
logging messages.

### Output Handling
- For each **output data object**, the processor must:
    1. Write the file to `wd_path`, using the **exact name** as declared in the processor's descriptor.
    2. Notify the job runner using `listener.on_output_available(output_name)`.
- The job runner will upload the output to the DOR (Data Object Repository) **only after** it receives 
the notification.

## `interrupt()` Method
Called by the job runner to **gracefully terminate** the processor early (e.g., due to 
cancellation or timeout). 
- The method should set a flag or use an event to signal termination. 
- The `run()` method should regularly check this flag and exit cleanly if the processor is interrupted. 
- If not handled gracefully, the job runner may forcibly stop the container.

## Example
Here is an example of a `processor.py` file:
```python
from simaas.core.processor import ProcessorBase, ProgressListener
import os
import json
import threading

class MyProcessor(ProcessorBase):
    def __init__(self):
        self._stop_requested = threading.Event()

    def run(self, wd_path, job, listener: ProgressListener, namespace, logger):
        input_path = os.path.join(wd_path, "input_data")
        output_path = os.path.join(wd_path, "result")

        # Read input
        with open(input_path, "r") as f:
            data = json.load(f)

        if self._stop_requested.is_set():
            logger.warning("Interrupted before processing started.")
            return

        # Simulate processing
        result = {"value": data["value"] * 2}
        with open(output_path, "w") as f:
            json.dump(result, f)

        # Notify output availability
        listener.on_output_available("result")
        listener.on_progress_update(1.0, "Processing complete.")

    def interrupt(self):
        self._stop_requested.set()
```

## Best Practices
✅ Use logger for structured output—this integrates with the runner’s logging system.
✅ Keep output filenames exact - they must match those in the descriptor.
✅ Use listener methods to:
- Send progress updates 
- Publish output availability 
- Log job-specific messages
✅ Gracefully handle interrupt() by checking for a stop flag.
❌ Don’t write files outside `wd_path`, only the working directory is persisted.
❌ Don’t ignore job metadata - e.g., input/output names, user context.

## Summary
The job runner sets up and manages the environment, then executes your processor class. 
The processor is expected to do the work, report progress, and return cleanly—or handle 
shutdown gracefully via `interrupt()`. Input and output handling is file-based, and 
communication is done through the provided listener and logger interfaces.