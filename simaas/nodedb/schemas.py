from typing import Tuple, Optional, List, Dict

from pydantic import BaseModel, Field

from simaas.core.identity import Identity


class NodeInfo(BaseModel):
    """
    Information about a SaaS node in the network.
    """
    identity: Identity = Field(..., title="Node Identity", description="The identity of the node.")
    last_seen: int = Field(..., title="Last Seen", description="The timestamp (in UTC milliseconds since the beginning of the epoch) when the node was seen last.", examples=[1664849510076])
    dor_service: str = Field(..., title="DOR Service", description="Indicates what DOR service is provided (if any).", examples=['basic'])
    rti_service: str = Field(..., title="RTI Service", description="Indicates if the node provides a RTI service.", examples=['docker'])
    p2p_address: str = Field(..., title="P2P Address", description="The address of the P2P service.", examples=['tcp://127.0.0.1:4001'])
    rest_address: Optional[Tuple[str, int]] = Field(title="REST Address", description="The address of the REST service (if applicable - not all nodes have the REST interface enabled).", examples=[('127.0.0.1', 5001)])
    retain_job_history: Optional[bool] = Field(title="Retain History", description="Indicates if the node retains the job history (only applicable to full or execution nodes that offer RTI services).", examples=[True])
    strict_deployment: Optional[bool] = Field(title="Strict Deployment", description="Indicates if the node restricts (un)deployment of processors to the node owner only (only applicable to full or execution nodes that offer RTI services).", examples=[True])

    def has_dor(self) -> bool:
        # dor_service is the literal string 'none' when no DOR is present (which is truthy);
        # a falsy value (e.g. empty string) would also count as absent.
        return bool(self.dor_service) and self.dor_service.lower() != 'none'

    def has_rti(self) -> bool:
        return bool(self.rti_service) and self.rti_service.lower() != 'none'


class ResourceDescriptor(BaseModel):
    vcpus: int = Field(..., title="VCPUs", description="The number of virtual CPUs.")
    memory: int = Field(..., title="Memory", description="The amount of memory (in megabytes).")


class NamespaceInfo(BaseModel):
    """
    Information about a namespace.
    """
    name: str = Field(..., title="Name", description="The name of the namespace.")
    budget: ResourceDescriptor = Field(..., title="Budget", description="The resource budget allocated to this namespace.")
    reservations: Dict[str, ResourceDescriptor] = Field(..., title="Reservations", description="Reserved resources that have not yet been claimed.")
    jobs: List[str] = Field(..., title="Job Ids", description="A list of job ids that are associated with this namespace.")
