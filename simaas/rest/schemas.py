from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union, Tuple

from pydantic import BaseModel


@dataclass
class EndpointDefinition:
    method: str
    prefix: Union[str, Tuple[str, str]]
    rule: str
    function: Any
    response_model: Any
    dependencies: Optional[Sequence[Any]]


class Token(BaseModel):
    access_token: str
    token_type: str
    expiry: int
