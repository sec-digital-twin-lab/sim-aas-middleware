"""
Simplified exception hierarchy for Sim-aaS middleware.

This module provides a clean, structured set of exceptions with automatic
ID generation, JSON-serializable details, and compatibility with the
existing ExceptionContent model.
"""

import json
from typing import Any, Optional

from simaas.core.exceptions import ExceptionContent
from simaas.core.helpers import generate_random_string
from simaas.core.logging import Logging

logger = Logging.get('simaas.core')

__all__ = [
    '_BaseError',  # Exported for REST handler catch-all
    'ConfigurationError',
    'AuthenticationError',
    'AuthorisationError',
    'NotFoundError',
    'NetworkError',
    'OperationError',
    'ValidationError',
    'InternalError',
]


class _BaseError(Exception):
    """Base class for domain exceptions. Don't instantiate directly - use specific types."""

    def __init__(self, **kwargs: Any) -> None:
        # Extract and store hint separately for easy access
        self._hint: Optional[str] = kwargs.get('hint')

        # Generate unique ID
        self._id = generate_random_string(16)

        # Store all kwargs as details
        self._details = dict(kwargs)

        # Generate reason string via subclass method
        self._reason = self._format_reason(**kwargs)

        # Ensure JSON compatibility
        self._ensure_json_compatible()

        # Call parent with reason as message
        super().__init__(self._reason)

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        """
        Generate a human-readable reason string.

        Override in subclasses to provide custom formatting.
        """
        return f"{cls.__name__}: {kwargs}"

    def _ensure_json_compatible(self) -> None:
        """Ensure details dict is JSON serializable, converting if needed."""
        try:
            json.dumps(self._details)
        except TypeError:
            logger.warning(
                f"Encountered JSON incompatible exception details: "
                f"class={self.__class__.__name__} id={self._id} details: {self._details}"
            )
            # Convert non-serializable values to strings
            self._details = {k: self._make_json_safe(v) for k, v in self._details.items()}

    @classmethod
    def _make_json_safe(cls, value: Any) -> Any:
        """Convert a value to a JSON-safe representation recursively."""
        # Handle None and basic types first
        if value is None or isinstance(value, (bool, int, float, str)):
            return value

        # Handle lists
        if isinstance(value, list):
            return [cls._make_json_safe(item) for item in value]

        # Handle dicts
        if isinstance(value, dict):
            return {k: cls._make_json_safe(v) for k, v in value.items()}

        # Try to serialize; if it fails, convert to string
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    @property
    def id(self) -> str:
        """Unique identifier for this exception instance."""
        return self._id

    @property
    def reason(self) -> str:
        """Human-readable reason string."""
        return self._reason

    @property
    def details(self) -> dict:
        """Structured details about the exception."""
        return self._details

    @property
    def hint(self) -> Optional[str]:
        """Optional hint for resolving the issue."""
        return self._hint

    @property
    def content(self) -> ExceptionContent:
        """ExceptionContent model for API compatibility."""
        return ExceptionContent(
            id=self._id,
            reason=self._reason,
            details=self._details
        )


class ConfigurationError(_BaseError):
    """Invalid or missing configuration. Args: path, expected, actual, hint."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        path = kwargs.get('path', 'unknown')
        expected = kwargs.get('expected')

        parts = [f"Configuration error at '{path}'"]
        if expected is not None:
            parts.append(f"expected {expected}")
        if 'actual' in kwargs:
            parts.append(f"got {kwargs['actual']}")

        return ": ".join(parts) if len(parts) > 1 else parts[0]


class AuthenticationError(_BaseError):
    """Authentication failed. Args: identity_id, operation, hint."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        identity_id = kwargs.get('identity_id', 'unknown')
        operation = kwargs.get('operation')

        if operation:
            return f"Authentication failed for '{identity_id}' during '{operation}'"
        return f"Authentication failed for '{identity_id}'"


class AuthorisationError(_BaseError):
    """Authorisation denied. Args: identity_id, resource_id, required_permission."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        identity_id = kwargs.get('identity_id', 'unknown')
        resource_id = kwargs.get('resource_id')
        required_permission = kwargs.get('required_permission')

        parts = [f"Authorisation denied for '{identity_id}'"]
        if resource_id:
            parts.append(f"on resource '{resource_id}'")
        if required_permission:
            parts.append(f"(requires '{required_permission}')")

        return " ".join(parts)


class NotFoundError(_BaseError):
    """Resource not found. Args: resource_type, resource_id, searched_locations, hint."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        resource_type = kwargs.get('resource_type', 'resource')
        resource_id = kwargs.get('resource_id', 'unknown')
        searched_locations = kwargs.get('searched_locations')

        reason = f"{resource_type} '{resource_id}' not found"
        if searched_locations:
            if isinstance(searched_locations, list):
                locations = ", ".join(str(loc) for loc in searched_locations)
            else:
                locations = str(searched_locations)
            reason += f" (searched: {locations})"

        return reason


class NetworkError(_BaseError):
    """Network operation failed. Args: peer_address, operation, timeout_ms, hint."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        peer_address = kwargs.get('peer_address', 'unknown')
        operation = kwargs.get('operation')
        timeout_ms = kwargs.get('timeout_ms')

        parts = [f"Network error with peer '{peer_address}'"]
        if operation:
            parts[0] = f"Network error during '{operation}' with peer '{peer_address}'"
        if timeout_ms:
            parts.append(f"(timeout: {timeout_ms}ms)")

        return " ".join(parts)


class OperationError(_BaseError):
    """Operation failed. Args: operation, stage, cause, hint."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        operation = kwargs.get('operation', 'unknown')
        stage = kwargs.get('stage')
        cause = kwargs.get('cause')

        parts = [f"Operation '{operation}' failed"]
        if stage:
            parts.append(f"at stage '{stage}'")
        if cause:
            parts.append(f": {cause}")

        return " ".join(parts) if not cause else "".join(parts)


class ValidationError(_BaseError):
    """Validation failed. Args: field, expected, actual, hint."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        field = kwargs.get('field', 'unknown')
        expected = kwargs.get('expected')
        actual = kwargs.get('actual')

        parts = [f"Validation failed for field '{field}'"]
        if expected is not None:
            parts.append(f"expected {expected}")
        if actual is not None:
            parts.append(f"got {actual}")

        return ": ".join(parts) if len(parts) > 1 else parts[0]


class InternalError(_BaseError):
    """Internal/unexpected error. Args: component, state, trace."""

    @classmethod
    def _format_reason(cls, **kwargs: Any) -> str:
        component = kwargs.get('component', 'unknown')
        state = kwargs.get('state')

        reason = f"Internal error in component '{component}'"
        if state:
            reason += f": {state}"

        return reason
