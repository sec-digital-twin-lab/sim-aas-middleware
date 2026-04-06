"""CLI helper utilities.

This module re-exports all helpers for backwards compatibility.
New code should import from specific submodules.
"""

# Base classes
from simaas.cli.helpers.base import (
    Argument,
    CLIExecutable,
    CLICommand,
    CLICommandGroup,
    CLIArgumentParser,
    CLIParser,
)

# Prompts
from simaas.cli.helpers.prompts import (
    prompt_for_string,
    prompt_for_integer,
    prompt_for_password,
    prompt_for_selection,
    prompt_for_confirmation,
    deserialise_tag_value,
    prompt_for_tags,
    prompt_if_missing,
    default_if_missing,
)

# Address utilities
from simaas.cli.helpers.address import (
    extract_address,
    ensure_address,
)

# Keystore utilities
from simaas.cli.helpers.keystore import (
    get_available_keystores,
    prompt_for_keystore_selection,
    prompt_for_identity_selection,
    load_keystore,
)

# Output utilities
from simaas.cli.helpers.output import (
    shorten_id,
    label_data_object,
    label_identity,
    table,
    print_result,
)

# Time utilities
from simaas.cli.helpers.time import (
    parse_period,
)

# Storage utilities
from simaas.cli.helpers.storage import (
    initialise_storage_folder,
)

# Data object utilities
from simaas.cli.helpers.data_objects import (
    prompt_for_data_objects,
)

# Network utilities
from simaas.cli.helpers.network import (
    get_nodes_by_service,
    env_if_missing,
    use_env_or_prompt_if_missing,
)


__all__ = [
    # Base classes
    'Argument',
    'CLIExecutable',
    'CLICommand',
    'CLICommandGroup',
    'CLIArgumentParser',
    'CLIParser',
    # Prompts
    'prompt_for_string',
    'prompt_for_integer',
    'prompt_for_password',
    'prompt_for_selection',
    'prompt_for_confirmation',
    'deserialise_tag_value',
    'prompt_for_tags',
    'prompt_if_missing',
    'default_if_missing',
    # Address
    'extract_address',
    'ensure_address',
    # Keystore
    'get_available_keystores',
    'prompt_for_keystore_selection',
    'prompt_for_identity_selection',
    'load_keystore',
    # Output
    'shorten_id',
    'label_data_object',
    'label_identity',
    'table',
    'print_result',
    # Time
    'parse_period',
    # Storage
    'initialise_storage_folder',
    # Data objects
    'prompt_for_data_objects',
    # Network
    'get_nodes_by_service',
    'env_if_missing',
    'use_env_or_prompt_if_missing',
]
