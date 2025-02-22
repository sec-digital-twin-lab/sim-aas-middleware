from enum import Enum
from typing import Literal, Optional, List, Union, Dict

from pydantic import BaseModel, Field

from simaas.core.exceptions import ExceptionContent
from simaas.dor.schemas import GitProcessorPointer, DataObject
from simaas.nodedb.schemas import NodeInfo


class Task(BaseModel):
    """
    Information about a task. This includes a processor, and details about the input and output consumed and produced
    as part of this task.
    """
    class InputReference(BaseModel):
        name: str = Field(..., title="Name", description="The name of the input (needs to exactly match the input as defined by the processor).")
        type: Literal["reference"] = Field(..., title="Type", description="Must be 'reference' for by-reference inputs.", examples=["reference"])
        obj_id: str = Field(..., title="Object Id", description="The id of the object to be used for this input.")
        user_signature: Optional[str] = Field(title="User Signature", description="A valid signature by the identity who owns the task. The RTI will use this signature to verify the user has access to the data object. User signature is only relevant and needed in case the referenced data object has restricted access.")
        c_hash: Optional[str] = Field(title="Content Hash", description="The content hash of this input.")

    class InputValue(BaseModel):
        name: str = Field(..., title="Name", description="The name of the input (needs to exactly match the input as defined by the processor).")
        type: Literal["value"] = Field(..., title="Type", description="Must be 'value' for by-value inputs.", examples=["value"])
        value: dict = Field(..., title="Value", description="The actual content or value of this input. This can be a JSON object.")

    class Output(BaseModel):
        name: str = Field(..., title="Name", description="The name of the output (needs to exactly match the output as defined by the processor).")
        owner_iid: str = Field(..., title="Owner IId", description="The id of the identity who will be the owner of this output data object once it has been created as part of this task.")
        restricted_access: bool = Field(..., title="Access Restricted", description="Indicates if access to the data object content should be restricted.", examples=[False])
        content_encrypted: bool = Field(..., title="Content Encrypted", description="Indicates if the content of this data object should be encrypted using the owner's public encryption key.", examples=[False])
        target_node_iid: Optional[str] = Field(title="Target Node IId", description="The id of the target node's identity for storing the output data objects")

    class Budget(BaseModel):
        vcpus: int = Field(..., title="VCPUs", description="The number of virtual CPUs allocated for this task.")
        memory: int = Field(..., title="Memory", description="The amount of memory (in megabytes) allocated for this task.")

    proc_id: str = Field(..., title="Processor Id", description="The id of the processor to be used for this task.")
    user_iid: str = Field(..., title="User IId", description="The id of the user's identity who owns this task.")
    input: List[Union[InputReference, InputValue]] = Field(..., title="Input", description="Information needed for every input defined by the processor.")
    output: List[Output] = Field(..., title="Output", description="Information needed for every output defined by the processor.")
    name: Optional[str] = Field(title="Name", description="The optional name of this task.")
    description: Optional[str] = Field(title="Description", description="The optional description of this task.")
    budget: Optional[Budget] = Field(title="Budget", description="The optional resource budget for this task.")


class Job(BaseModel):
    """
    Information about a job.
    """
    id: str = Field(..., title="Id", description="The job id.", examples=["Ikn7dPv6"])
    task: Task = Field(..., title="Task", description="The task of this job")
    retain: bool = Field(..., title="Retain", description="Indicates if the RTI should retain the working directory of this job. This is only used for debugging and testing purposes.", examples=[False])
    custodian: NodeInfo = Field(..., title='Custodian', description="Information about the node that hosts this job.")
    proc_name: str = Field(..., title="Processor Name", description="The name of the processor.")
    t_submitted: int = Field(..., title="Time Submitted", description="The timestamp (UTC in milliseconds since the beginning of the epoch) when the job was submitted.")


class ExitCode(str, Enum):
    DONE = 'done',
    INTERRUPTED = 'interrupted',
    ERROR = 'error'


class JobResult(BaseModel):
    exitcode: ExitCode
    trace: Optional[str]


class Severity(str, Enum):
    DEBUG = 'debug'
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'


class JobStatus(BaseModel):
    """
    Status information about a job.
    """

    class Error(BaseModel):
        """
        Information about an error.
        """
        message: str = Field(..., title="Message", description="A simple message indicating the nature of the problem.")
        exception: ExceptionContent = Field(..., title="Exception", description="Detailed information about an exception that occured during job execution.")

    class Message(BaseModel):
        """
        A message with severity level.
        """
        severity: Severity
        content: str

    class State(str, Enum):
        """
        The possible states of a job.
        """
        UNINITIALISED = 'uninitialised'
        INITIALISED = 'initialised'
        RUNNING = 'running'
        # ----
        SUCCESSFUL = 'successful'
        FAILED = 'failed'
        CANCELLED = 'cancelled'

    state: State = Field(..., title="State", description="The state of the job.", examples=['running'])
    progress: int = Field(..., title="Progress", description="An integer value indicating the progress in %.", examples=[55])
    output: Dict[str, Optional[DataObject]] = Field(..., title="Output", description="A mapping of product names (i.e., the outputs of the job) and the corresponding object meta information.")
    notes: dict = Field(..., title="Notes", description="Any notes that may have been logged during the execution.")
    errors: List[Error] = Field(..., title="Errors", description="A list of errors that occurred during job execution (if any)")
    message: Optional[Message] = Field(title="Message", description="A message providing more details about its current status (if any)")


class Processor(BaseModel):
    class State(str, Enum):
        """
        The possible states of a processor.
        """
        BUSY_DEPLOY = 'busy_deploy'
        BUSY_UNDEPLOY = 'busy_undeploy'
        READY = 'ready'
        FAILED = 'failed'

    """
    Information about a processor.
    """
    id: str = Field(..., title="Processor Id", description="The processor id.", examples=["d01d069675bcaaeb90b46273ccc4ae9818a2667957045d0f0f15901ffcf807de"])
    state: Literal[State.BUSY_DEPLOY, State.BUSY_UNDEPLOY, State.READY, State.FAILED] = Field(..., title="State", description="The state of the processor.")
    image_name: Optional[str] = Field(title="Image Name", description="The name of the docker image that contains the processor.")
    gpp: Optional[GitProcessorPointer] = Field(title="GPP", description="The Git Processor Pointer information.")
    error: Optional[str] = Field(..., title="Error", description="Information about the error encountered (only if state is 'failed').")


class ProcessorStatus(BaseModel):
    """
    Status information about a deployed processor.
    """
    state: str = Field(..., title="State", description="The state of the processor.", examples=["initialised"])
    pending: List[Job] = Field(..., title="Pending Jobs", description="A list of pending jobs that are queued for execution.")
    active: List[Job] = Field(..., title="Active Jobs", description="A list of active jobs that are currently being executed by the processor (if any).")
