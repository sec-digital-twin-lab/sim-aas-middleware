import json
from typing import Optional

from pydantic import BaseModel, Field

from simaas.core.helpers import generate_random_string
from simaas.core.logging import Logging

logger = Logging.get('simaas.core')


class ExceptionContent(BaseModel):
    """
    The content of an exception.
    """
    id: str = Field(..., title="Id", description="The unique identifier of this exception.")
    reason: str = Field(..., title="Reason", description="The reason that caused this exception.")
    details: Optional[dict] = Field(title="Details", description="Supporting information about this exception.")


class SaaSRuntimeException(Exception):
    def __init__(self, reason: str, details: dict = None, id: str = None):
        self._content = ExceptionContent(id=id if id else generate_random_string(16), reason=reason, details=details)

        # check if the details can be JSON encoded
        try:
            json.dumps(self._content.dict())
        except TypeError:
            logger.warning(f"Encountered JSON incompatible exception details: class={self.__class__.__name__} "
                           f"id={self._content.id} details: {self._content.details}")

            # convert detail values into strings as a fallback
            self._content.details = {
                k: str(v) for k, v in self._content.details.items()
            }
        except Exception as e:
            logger.error(f"Encountered JSON incompatible exception content: {e} {reason} {details}")

    @property
    def id(self):
        return self._content.id

    @property
    def reason(self):
        return self._content.reason

    @property
    def details(self):
        return self._content.details

    @property
    def content(self) -> ExceptionContent:
        return self._content
