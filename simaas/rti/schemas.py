from enum import Enum
from typing import Literal, Optional, List, Union, Dict

from pydantic import BaseModel, Field

from simaas.core.exceptions import ExceptionContent
from simaas.core.identity import Identity
from simaas.dor.schemas import GitProcessorPointer, DataObject
from simaas.nodedb.schemas import NodeInfo


class Task(BaseModel):
    """
    Information about a task. This includes a processor, and details about the input and output consumed and produced
    as part of this task.
    """
    class InputReference(BaseModel):
        name: str = Field(..., title="Name", description="The name of the input (needs to exactly match the input as defined by the processor).")
        type: Literal["reference"] = Field(..., title="Type", description="Must be 'reference' for by-reference inputs.", example="reference")
        obj_id: str = Field(..., title="Object Id", description="The id of the object to be used for this input.")
        user_signature: Optional[str] = Field(title="User Signature", description="A valid signature by the identity who owns the task. The RTI will use this signature to verify the user has access to the data object. User signature is only relevant and needed in case the referenced data object has restricted access.")
        c_hash: Optional[str] = Field(title="Content Hash", description="The content hash of this input.")

    class InputValue(BaseModel):
        name: str = Field(..., title="Name", description="The name of the input (needs to exactly match the input as defined by the processor).")
        type: Literal["value"] = Field(..., title="Type", description="Must be 'value' for by-value inputs.", example="value")
        value: dict = Field(..., title="Value", description="The actual content or value of this input. This can be a JSON object.")

    class Output(BaseModel):
        name: str = Field(..., title="Name", description="The name of the output (needs to exactly match the output as defined by the processor).")
        owner_iid: str = Field(..., title="", description="The id of the identity who will be the owner of this output data object once it has been created as part of this task.")
        restricted_access: bool = Field(..., title="Access Restricted", description="Indicates if access to the data object content should be restricted.", example=False)
        content_encrypted: bool = Field(..., title="Content Encrypted", description="Indicates if the content of this data object should be encrypted using the owner's public encryption key.", example=False)
        target_node_iid: Optional[str] = Field(title="", description="", example="")

    proc_id: str = Field(..., title="Processor Id", description="The id of the processor to be used for this task.")
    user_iid: str = Field(..., title="User IId", description="The id of the user's identity who owns this task.")
    input: List[Union[InputReference, InputValue]] = Field(..., title="Input", description="Information needed for every input defined by the processor.")
    output: List[Output] = Field(..., title="Output", description="Information needed for every output defined by the processor.")
    name: Optional[str] = Field(title="Name", description="The optional name of this task.")
    description: Optional[str] = Field(title="Description", description="The optional description of this task.")


class Job(BaseModel):
    """
    Information about a job.
    """
    id: str = Field(..., title="Id", description="The job id.", example="Ikn7dPv6")
    task: Task = Field(..., title="Task", description="The task of this job")
    retain: bool = Field(..., title="Retain", description="Indicates if the RTI should retain the working directory of this job. This is only used for debugging and testing purposes.", example=False)
    custodian: NodeInfo = Field(..., title='Custodian', description="Information about the node that hosts this job.")
    owner: Identity = Field(..., title='Owner', description="Identity of the owner of this job.")
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
        PREPROCESSING = 'preprocessing'
        RUNNING = 'running'
        POSTPROCESSING = 'postprocessing'
        # ----
        SUCCESSFUL = 'successful'
        FAILED = 'failed'
        CANCELLED = 'cancelled'

    state: State = Field(..., title="State", description="The state of the job.", example='running')
    progress: int = Field(..., title="Progress", description="An integer value indicating the progress in %.", example=55)
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
    id: str = Field(..., title="Processor Id", description="The processor id.", example="d01d069675bcaaeb90b46273ccc4ae9818a2667957045d0f0f15901ffcf807de")
    state: Literal[State.BUSY_DEPLOY, State.BUSY_UNDEPLOY, State.READY, State.FAILED] = Field(..., title="State", description="The state of the processor.")
    image_name: Optional[str] = Field(title="Image Name", description="The name of the docker image that contains the processor.")
    gpp: Optional[GitProcessorPointer] = Field(title="GPP", description="The Git Processor Pointer information.")
    error: Optional[str] = Field(..., title="Error", description="Information about the error encountered (only if state is 'failed').")


class ProcessorStatus(BaseModel):
    """
    Status information about a deployed processor.
    """
    state: str = Field(..., title="State", description="The state of the processor.", example="initialised")
    pending: List[Job] = Field(..., title="Pending Jobs", description="A list of pending jobs that are queued for execution.")
    active: List[Job] = Field(..., title="Active Jobs", description="A list of active jobs that are currently being executed by the processor (if any).")
