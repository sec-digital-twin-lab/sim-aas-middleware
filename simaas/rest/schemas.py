from dataclasses import dataclass
from typing import Any, Union, Tuple

from pydantic import BaseModel, Field


@dataclass
class EndpointDefinition:
    method: str
    prefix: Union[str, Tuple[str, str]]
    rule: str
    function: Any
    response_model: Any


class Token(BaseModel):
    access_token: str = Field(..., repr=False)
    token_type: str
    expiry: int

    def __repr__(self) -> str:
        return f"Token(access_token=<redacted>, token_type={self.token_type!r}, expiry={self.expiry})"
