from dataclasses import dataclass
from typing import Any, Union, Tuple

from pydantic import BaseModel


@dataclass
class EndpointDefinition:
    method: str
    prefix: Union[str, Tuple[str, str]]
    rule: str
    function: Any
    response_model: Any


class Token(BaseModel):
    access_token: str
    token_type: str
    expiry: int
