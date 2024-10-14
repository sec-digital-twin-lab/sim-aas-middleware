from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from abc import abstractmethod, ABC
from argparse import ArgumentParser
from json import JSONDecodeError
from typing import Optional, Union, List, Any

from InquirerPy import inquirer
from InquirerPy.base import Choice
from pydantic import ValidationError

from simaas.cli.exceptions import CLIRuntimeError
from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.helpers import determine_default_rest_address
from simaas.dor.schemas import DataObject
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import NodeInfo
from simaas.core.schemas import KeystoreContent
from simaas.rest.exceptions import UnsuccessfulRequestError

logger = Logging.get('cli')


def shorten_id(long_id: str) -> str:
    return f'{long_id[:4]}...{long_id[-4:]}'


def label_data_object(meta: DataObject) -> str:
    tags = []
    for key, value in meta.tags.items():
        if value:
            tags.append(f"{key}={value if isinstance(value, (str, bool, int, float)) else '...'}")
        else:
            tags.append(key)

    return f"{shorten_id(meta.obj_id)} [{meta.data_type}:{meta.data_format}] {' '.join(tags)}"


def label_identity(identity: Identity, truncate: bool = False) -> str:
    if truncate:
        return f"{shorten_id(identity.id)} - {identity.name} <{identity.email}>"
    else:
        return f"{identity.id} - {identity.name} <{identity.email}>"


def initialise_storage_folder(path: str, usage: str, is_verbose: bool = False) -> None:
    # check if the path is pointing at a file
    if os.path.isfile(path):
        raise CLIRuntimeError(f"Storage path '{path}' is a file. This path cannot "
                              f"be used as storage ({usage}) directory.")

    # check if it already exists as directory
    if not os.path.isdir(path):
        logger.info(f"creating storage ({usage}) directory '{path}'")
        os.makedirs(path)
        if is_verbose:
            print(f"Storage directory ({usage}) created at '{path}'.")


def get_available_keystores(path: str, is_verbose: bool = False) -> List[KeystoreContent]:
    available = []
    for f in os.listdir(path):
        # eligible filename example: 9rak8e1tmc0xt4v3onwq7cpsydpselrjxqp2cys823alhu8evzkjhusqic740h39.json
        temp = os.path.basename(f).split(".")
        if len(temp) != 2 or temp[1].lower() != 'json' or len(temp[0]) != 64:
            continue

        # read content and validate
        try:
            content = KeystoreContent.parse_file(os.path.join(path, f))
            available.append(content)
        except ValidationError:
            logger.info(f"error while parsing {f}")
            if is_verbose:
                print(f"Error while parsing {f} -> Ignoring.")
            continue

    return available


def extract_address(address: str) -> (str, int):
    if address.count(':') != 1:
        raise CLIRuntimeError(f"Invalid address '{address}'")

    temp = address.split(':')
    if not temp[1].isdigit():
        raise CLIRuntimeError(f"Invalid address '{address}'")

    return temp[0], int(temp[1])


def prompt_for_keystore_selection(path: str, message: str) -> Optional[KeystoreContent]:
    # get all available keystores
    available = get_available_keystores(path)
    if len(available) == 0:
        raise CLIRuntimeError(f"No keystores found at '{path}'")

    choices = [Choice(value=item.iid, name=f"{item.profile.name}/{item.profile.email}/{item.iid}",)
               for item in available]
    return prompt_for_selection(choices, message)


def prompt_for_identity_selection(address: (str, int), message: str,
                                  allow_multiple: bool = False) -> Union[Identity, List[Identity]]:

    # get all identities known to the node
    proxy = NodeDBProxy(address)
    choices = [
        Choice(identity, f"{identity.name}/{identity.email}/{identity.id}")
        for identity in proxy.get_identities().values()
    ]

    # prompt for selection
    if len(choices) == 0:
        raise CLIRuntimeError(f"No identities found at '{address}'")

    return prompt_for_selection(choices, message, allow_multiple=allow_multiple)


def load_keystore(args: dict, ensure_publication: bool, address_arg: str = 'address') -> Keystore:
    # prompt for the keystore and id (if missing)
    prompt_if_missing(args, 'keystore-id', prompt_for_keystore_selection,
                      path=args['keystore'],
                      message="Select the keystore:")

    prompt_if_missing(args, 'password', prompt_for_password, confirm=False)

    # try to unlock the keystore
    try:
        keystore = Keystore.from_file(os.path.join(args['keystore'], f"{args['keystore-id']}.json"), args['password'])

    except SaaSRuntimeException as e:
        raise CLIRuntimeError(f"Could not open keystore {args['keystore-id']} because '{e.reason}'. Aborting.")

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

        except UnsuccessfulRequestError as e:
            raise CLIRuntimeError(f"Could not ensure identity is known to node at {args[address_arg]}. Aborting. "
                                  f"(Hint: {e.reason})")

    return keystore


def prompt_for_string(message: str, default: str = '', hide: bool = False, allow_empty: bool = False) -> str:
    questions = [
        {
            'type': 'password' if hide else 'input',
            'message': message,
            'name': 'answer',
        }
    ]

    # set the default (if any)
    if default:
        questions[0]['default'] = default

    while True:
        if hide:
            answer = inquirer.secret(message=message, default=default).execute()
        else:
            answer = inquirer.text(message=message, default=default).execute()

        if answer or allow_empty:
            return answer


def prompt_for_password(confirm: bool = True, allow_empty: bool = False) -> str:
    while True:
        pwd1 = prompt_for_string("Enter password:", hide=True, allow_empty=allow_empty)
        if confirm:
            pwd2 = prompt_for_string("Re-enter password:", hide=True, allow_empty=allow_empty)
            if pwd1 != pwd2:
                print("Passwords don't match! Please try again.")
                continue

        return pwd1


def prompt_for_selection(choices: List[Choice], message: str, allow_multiple=False) -> Union[Any, List[Any]]:
    if allow_multiple:
        return inquirer.checkbox(
            message=message,
            choices=choices,
            cycle=False,
            show_cursor=False
        ).execute()

    else:
        return inquirer.select(
            message=message,
            choices=choices,
            cycle=False,
            show_cursor=False
        ).execute()


def prompt_for_confirmation(message: str, default: bool) -> bool:
    return inquirer.confirm(message=message, default=default).execute()


def deserialise_tag_value(tag: DataObject.Tag) -> DataObject.Tag:
    if tag.value.isdigit():
        tag.value = int(tag.value)

    elif tag.value.replace('.', '', 1).isdigit():
        tag.value = float(tag.value)

    elif tag.value.lower() in ['true', 'false']:
        tag.value = tag.value.lower() == 'true'

    else:
        try:
            tag.value = json.loads(tag.value)
        except JSONDecodeError:
            pass

    return tag


def prompt_for_tags(message: str) -> List[DataObject.Tag]:
    answers = []
    while True:
        answer = prompt_for_string(message, allow_empty=True)
        if not answer:
            return answers

        elif answer.count('=') > 1:
            print("Invalid tag. Use key=value form. Must not contain more than one '=' character. Try again...")

        elif answer.count('=') == 0:
            answers.append(DataObject.Tag(key=answer))

        else:
            answer = answer.split('=')
            answers.append(deserialise_tag_value(DataObject.Tag(key=answer[0], value=answer[1])))


def prompt_for_data_objects(address: (str, int), message: str, filter_by_owner: Identity = None,
                            allow_multiple=False) -> Union[str, List[str]]:

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


def prompt_if_missing(args: dict, key: str, function, **func_args) -> Any:
    if args.get(key, None) is None:
        args[key] = function(**func_args)
    return args[key]


def default_if_missing(args: dict, key: str, default: Any) -> Any:
    if args.get(key, None) is None:
        args[key] = default
    return args[key]


def get_nodes_by_service(address: [str, int]) -> (List[NodeInfo], List[NodeInfo]):
    dor_nodes = []
    rti_nodes = []
    db = NodeDBProxy(address)
    for node in db.get_network():
        if node.dor_service:
            dor_nodes.append(node)

        if node.rti_service:
            rti_nodes.append(node)

    return dor_nodes, rti_nodes


class Argument:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


class CLIExecutable(ABC):
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def help(self) -> str:
        pass

    @abstractmethod
    def initialise(self, parser: ArgumentParser) -> None:
        pass

    @abstractmethod
    def execute(self, args: dict) -> Optional[dict]:
        pass


class CLICommand(CLIExecutable, ABC):
    def __init__(self, name: str, description: str, arguments: list[Argument] = None) -> None:
        self._name = name
        self._description = description
        self._arguments = arguments if arguments else []

    def name(self) -> str:
        return self._name

    def help(self) -> str:
        return self._description

    def initialise(self, parser: ArgumentParser) -> None:
        for a in self._arguments:
            parser.add_argument(*a.args, **a.kwargs)


class CLICommandGroup(CLIExecutable, ABC):
    def __init__(self, name: str, description: str,
                 arguments: list[Argument] = None, commands: list[CLIExecutable] = None) -> None:
        self._name = name
        self._tag = f"cmd_{name}"
        self._description = description
        self._arguments = arguments if arguments else []
        self._commands = commands if commands else []
        self._c_map: dict[str, CLIExecutable] = {}

    def name(self) -> str:
        return self._name

    def help(self) -> str:
        return self._description

    def initialise(self, parser: ArgumentParser) -> None:
        for a in self._arguments:
            parser.add_argument(*a.args, **a.kwargs)

        subparsers = parser.add_subparsers(title='Available commands', metavar=self._tag, dest=self._tag, required=True)
        for c in self._commands:
            c_parser = subparsers.add_parser(c.name(), help=c.help())
            c.initialise(c_parser)
            self._c_map[c.name()] = c

    def execute(self, args: dict) -> None:
        c_name = args[self._tag]
        command = self._c_map[c_name]
        command.execute(args)


class CLIArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_help()
        sys.exit(1)


class CLIParser(CLICommandGroup):
    def __init__(self, description, arguments: list[Argument] = None, commands: list[CLIExecutable] = None) -> None:
        super().__init__('main', description, arguments, commands)

    def execute(self, args: list) -> None:
        parser = CLIArgumentParser(description=self._description)

        try:
            self.initialise(parser)

            args = vars(parser.parse_args(args))

            if 'temp-dir' in args:
                initialise_storage_folder(args['temp-dir'], 'temp-dir')

            if 'keystore' in args:
                initialise_storage_folder(args['keystore'], 'keystore')

            if args['log-level'] == 'DEBUG':
                level = logging.DEBUG
            elif args['log-level'] == 'INFO':
                level = logging.INFO
            else:
                level = logging.INFO

            console_enabled = args['log-console'] is not None
            log_path = args['log-path']
            Logging.initialise(level=level, log_path=log_path, console_log_enabled=console_enabled)
            super().execute(args)

        except argparse.ArgumentError:
            parser.print_help()
