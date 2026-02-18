"""Data object selection and utility functions."""

from __future__ import annotations

from typing import Union, List

from InquirerPy.base import Choice

from simaas.core.identity import Identity
from simaas.dor.api import DORProxy
from simaas.cli.helpers.prompts import prompt_for_selection
from simaas.cli.helpers.output import label_data_object


def prompt_for_data_objects(address: tuple[str, int], message: str, filter_by_owner: Identity = None,
                            allow_multiple=False) -> Union[str, List[str]]:
    """Prompt user to select data object(s) from a DOR.

    Args:
        address: Node REST address as (host, port)
        message: Prompt message to display
        filter_by_owner: Optional identity to filter by owner
        allow_multiple: Whether to allow selecting multiple objects

    Returns:
        Selected object ID or list of IDs (empty list/None if no objects found)
    """
    # find all data objects owned by the identity
    dor = DORProxy(address)
    result = dor.search(owner_iid=filter_by_owner.id if filter_by_owner else None)

    # do we have any data objects?
    if len(result) == 0:
        return [] if allow_multiple else None

    # determine choices
    choices = [Choice(item.obj_id, label_data_object(item)) for item in result]

    # prompt for selection
    return prompt_for_selection(choices, message, allow_multiple)
