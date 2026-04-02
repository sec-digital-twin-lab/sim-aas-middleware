"""Address parsing and validation utilities."""

from __future__ import annotations

from typing import Tuple

from simaas.core.errors import CLIError
from simaas.cli.helpers.prompts import prompt_for_string, prompt_if_missing
from simaas.helpers import determine_default_rest_address


def extract_address(address: str) -> Tuple[str, int]:
    """Parse address string into (host, port) tuple.

    Args:
        address: Address string in format 'host:port'

    Returns:
        Tuple of (host, port)

    Raises:
        CLIError: If address format is invalid
    """
    if address.count(':') != 1:
        raise CLIError(f"Invalid address '{address}'")

    temp = address.split(':')
    if not temp[1].isdigit():
        raise CLIError(f"Invalid address '{address}'")

    return temp[0], int(temp[1])


def ensure_address(args: dict, key: str = 'address', message: str = None) -> Tuple[str, int]:
    """Prompt for address if missing, parse and validate.

    Args:
        args: Arguments dict to check and update
        key: Key name for address in args (default: 'address')
        message: Custom prompt message (default: "Enter the node's REST address")

    Returns:
        Tuple of (host, port)
    """
    if message is None:
        message = "Enter the node's REST address"

    prompt_if_missing(args, key, prompt_for_string,
                      message=message,
                      default=determine_default_rest_address())

    return extract_address(args[key])
