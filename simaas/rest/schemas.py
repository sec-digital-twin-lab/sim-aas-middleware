from dataclasses import dataclass
from typing import Any, Union, Tuple


@dataclass
class EndpointDefinition:
    method: str
    prefix: Union[str, Tuple[str, str]]
    rule: str
    function: Any
    response_model: Any
