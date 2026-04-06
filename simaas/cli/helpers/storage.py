"""Storage and file system utilities."""

from __future__ import annotations

import os

from simaas.core.errors import CLIError
from simaas.core.logging import get_logger

log = get_logger('simaas.cli', 'cli')


def initialise_storage_folder(path: str, usage: str, is_verbose: bool = False) -> None:
    """Initialize a storage directory, creating it if necessary.

    Args:
        path: Path to the storage directory
        usage: Description of what the directory is used for (for logging)
        is_verbose: Whether to print messages about directory creation

    Raises:
        CLIError: If path exists as a file
    """
    # check if the path is pointing at a file
    if os.path.isfile(path):
        raise CLIError(f"Storage path '{path}' is a file. This path cannot "
                       f"be used as storage ({usage}) directory.")

    # check if it already exists as directory
    if not os.path.isdir(path):
        log.info('storage', 'Creating storage directory', path=path, usage=usage)
        os.makedirs(path)
        if is_verbose:
            print(f"Storage directory ({usage}) created at '{path}'.")
