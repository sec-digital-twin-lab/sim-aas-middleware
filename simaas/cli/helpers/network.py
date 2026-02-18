"""Network-related helper functions."""

from __future__ import annotations

import os
from typing import List, Any

from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import NodeInfo


def get_nodes_by_service(address: tuple[str, int]) -> tuple[List[NodeInfo], List[NodeInfo]]:
    """Get nodes grouped by service type.

    Args:
        address: Node REST address as (host, port)

    Returns:
        Tuple of (dor_nodes, rti_nodes) lists
    """
    dor_nodes = []
    rti_nodes = []
    db = NodeDBProxy(address)
    for node in db.get_network():
        if node.dor_service and node.dor_service.lower() != 'none':
            dor_nodes.append(node)

        if node.rti_service and node.rti_service.lower() != 'none':
            rti_nodes.append(node)

    return dor_nodes, rti_nodes


def env_if_missing(args: dict, key: str, env_var: str) -> Any:
    """Set value from environment variable if key is missing from args.

    Args:
        args: Arguments dict to check and update
        key: Key name in args
        env_var: Environment variable name to read from

    Returns:
        The value (from args or environment)
    """
    if args.get(key, None) is None:
        args[key] = os.environ.get(env_var, None)
    return args[key]


def use_env_or_prompt_if_missing(args: dict, key: str, env_var: str, function, **func_args) -> Any:
    """Try environment variable first, then prompt if still missing.

    Args:
        args: Arguments dict to check and update
        key: Key name in args
        env_var: Environment variable name to try first
        function: Prompt function to call if still missing
        **func_args: Arguments to pass to prompt function

    Returns:
        The value (from args, environment, or prompt)
    """
    if args.get(key, None) is None:
        args[key] = os.environ.get(env_var, None)
        if args.get(key, None) is None:
            args[key] = function(**func_args)
    return args[key]
