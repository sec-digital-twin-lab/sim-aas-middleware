"""Keystore loading and selection utilities."""

from __future__ import annotations

import json
import os
from typing import Optional, List, Union

from InquirerPy.base import Choice
from pydantic import ValidationError

from simaas.core.errors import CLIError, _BaseError, RemoteError
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.schemas import KeystoreContent
from simaas.core.logging import get_logger
from simaas.cli.helpers.prompts import prompt_for_string, prompt_for_selection, prompt_for_password, prompt_if_missing
from simaas.cli.helpers.address import extract_address
from simaas.helpers import determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy


log = get_logger('simaas.cli', 'cli')


def get_available_keystores(path: str, is_verbose: bool = False) -> List[KeystoreContent]:
    """Get all valid keystores from a directory.

    Args:
        path: Directory path to search for keystores
        is_verbose: Whether to print messages about invalid keystores

    Returns:
        List of KeystoreContent objects for valid keystores
    """
    available = []
    for filename in os.listdir(path):
        # eligible filename example: 9rak8e1tmc0xt4v3onwq7cpsydpselrjxqp2cys823alhu8evzkjhusqic740h39.json
        temp = os.path.basename(filename).split(".")
        if len(temp) != 2 or temp[1].lower() != 'json' or len(temp[0]) != 64:
            continue

        # read content and validate
        try:
            with open(os.path.join(path, filename), 'r') as f:
                content = KeystoreContent.model_validate(json.load(f))
            available.append(content)
        except ValidationError:
            log.info('keystore', 'Error while parsing keystore file', filename=filename)
            if is_verbose:
                print(f"Error while parsing {filename}. Ignoring.")
            continue

    return available


def prompt_for_keystore_selection(path: str, message: str) -> Optional[KeystoreContent]:
    """Prompt user to select a keystore from available keystores.

    Args:
        path: Directory path containing keystores
        message: Prompt message to display

    Returns:
        Selected keystore ID

    Raises:
        CLIError: If no keystores found
    """
    # get all available keystores
    available = get_available_keystores(path)
    if len(available) == 0:
        raise CLIError(f"No keystores found at '{path}'")

    choices = [Choice(value=item.iid, name=f"{item.profile.name}/{item.profile.email}/{item.iid}",)
               for item in available]
    return prompt_for_selection(choices, message)


def prompt_for_identity_selection(address: tuple[str, int], message: str,
                                  allow_multiple: bool = False) -> Union[Identity, List[Identity]]:
    """Prompt user to select identity(ies) from a node.

    Args:
        address: Node REST address as (host, port)
        message: Prompt message to display
        allow_multiple: Whether to allow selecting multiple identities

    Returns:
        Selected identity or list of identities

    Raises:
        CLIError: If no identities found
    """
    # get all identities known to the node
    proxy = NodeDBProxy(address)
    choices = [
        Choice(identity, f"{identity.name}/{identity.email}/{identity.id}")
        for identity in proxy.get_identities().values()
    ]

    # prompt for selection
    if len(choices) == 0:
        raise CLIError(f"No identities found at '{address}'")

    return prompt_for_selection(choices, message, allow_multiple=allow_multiple)


def load_keystore(args: dict, ensure_publication: bool, address_arg: str = 'address') -> Keystore:
    """Load and optionally publish a keystore.

    Args:
        args: Arguments dict containing keystore path, id, and password
        ensure_publication: Whether to ensure identity is published to node
        address_arg: Key name for address in args

    Returns:
        Loaded Keystore object

    Raises:
        CLIError: If keystore cannot be opened or identity cannot be published
    """
    # prompt for the keystore and id (if missing)
    prompt_if_missing(args, 'keystore-id', prompt_for_keystore_selection,
                      path=args['keystore'],
                      message="Select the keystore:")

    prompt_if_missing(args, 'password', prompt_for_password, confirm=False)

    # try to unlock the keystore
    try:
        keystore = Keystore.from_file(os.path.join(args['keystore'], f"{args['keystore-id']}.json"), args['password'])

    except _BaseError as e:
        raise CLIError(f"Could not open keystore {args['keystore-id']} because '{e.reason}'. Aborting.")

    if ensure_publication:
        # prompt for the address (if missing)
        prompt_if_missing(args, address_arg, prompt_for_string,
                          message="Enter the node's REST address",
                          default=determine_default_rest_address())

        # try to ensure check if the identity is known and prompt to publish (if otherwise)
        try:
            # check if node knows about identity
            db = NodeDBProxy(args[address_arg].split(":"))
            if db.get_identity(keystore.identity.id) is None:
                db.update_identity(keystore.identity)
                print(f"Identity {keystore.identity.id} published to node at {args[address_arg]}.")

        except RemoteError as e:
            raise CLIError(f"Could not ensure identity is known to node at {args[address_arg]}. Aborting. "
                                  f"(Hint: {e.reason})")

    return keystore
