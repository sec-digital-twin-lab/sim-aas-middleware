"""Output formatting utilities for CLI commands."""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from tabulate import tabulate

from simaas.core.identity import Identity
from simaas.dor.schemas import DataObject


def shorten_id(long_id: str) -> str:
    """Shorten a long ID to first4...last4 format."""
    return f'{long_id[:4]}...{long_id[-4:]}'


def label_data_object(meta: DataObject) -> str:
    """Create a human-readable label for a data object."""
    tags = []
    for key, value in meta.tags.items():
        if value:
            tags.append(f"{key}={value if isinstance(value, (str, bool, int, float)) else '...'}")
        else:
            tags.append(key)

    return f"{shorten_id(meta.obj_id)} [{meta.data_type}:{meta.data_format}] {' '.join(tags)}"


def label_identity(identity: Identity, truncate: bool = False) -> str:
    """Create a human-readable label for an identity."""
    if truncate:
        return f"{shorten_id(identity.id)} - {identity.name} <{identity.email}>"
    else:
        return f"{identity.id} - {identity.name} <{identity.email}>"


def table(headers: List[str], rows: List[List[Any]], empty_msg: str = "No results found.") -> Optional[str]:
    """Build consistent table output with headers, separators, and empty handling.

    Args:
        headers: List of column header strings
        rows: List of row data (each row is a list of values)
        empty_msg: Message to return if no rows

    Returns:
        Formatted table string, or None if empty (prints empty_msg)
    """
    if not rows:
        print(empty_msg)
        return None

    # Create separator line matching header widths
    separators = ['-' * len(h) for h in headers]

    # Build table with headers, separator, and data
    lines = [headers, separators] + rows

    return tabulate(lines, tablefmt="plain")


def print_result(data: Any, json_mode: bool = False, table_fn: Callable = None,
                 empty_msg: str = "No results found.") -> None:
    """Print result as JSON or formatted table based on mode.

    Args:
        data: Data to print (list for table, any for JSON)
        json_mode: If True, print as JSON; if False, use table_fn
        table_fn: Function to format data as table (receives data, returns string)
        empty_msg: Message to print if data is empty
    """
    if json_mode:
        # JSON output mode
        if hasattr(data, 'model_dump'):
            # Pydantic model
            print(json.dumps(data.model_dump(), indent=2))
        elif isinstance(data, list):
            # List of items
            items = []
            for item in data:
                if hasattr(item, 'model_dump'):
                    items.append(item.model_dump())
                else:
                    items.append(item)
            print(json.dumps(items, indent=2))
        elif isinstance(data, dict):
            # Dict
            output = {}
            for k, v in data.items():
                if hasattr(v, 'model_dump'):
                    output[k] = v.model_dump()
                else:
                    output[k] = v
            print(json.dumps(output, indent=2))
        else:
            print(json.dumps(data, indent=2))
    else:
        # Table output mode
        if not data:
            print(empty_msg)
        elif table_fn:
            result = table_fn(data)
            if result:
                print(result)
        else:
            # Default: just print the data
            print(data)
