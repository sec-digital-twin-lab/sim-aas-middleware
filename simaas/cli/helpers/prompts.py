"""Interactive prompt utilities using InquirerPy."""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Union, List, Any

from InquirerPy import inquirer
from InquirerPy.base import Choice

from simaas.dor.schemas import DataObject


def prompt_for_string(message: str, default: str = '', hide: bool = False, allow_empty: bool = False) -> str:
    """Prompt user for a string input."""
    while True:
        if hide:
            answer = inquirer.secret(message=message, default=default).execute()
        else:
            answer = inquirer.text(message=message, default=default).execute()

        if answer or allow_empty:
            return answer


def prompt_for_integer(message: str, default: int = 0) -> int:
    """Prompt user for an integer input."""
    while True:
        answer = inquirer.text(message=message, default=str(default)).execute()

        try:
            return int(answer)

        except ValueError:
            message = f"Not a valid integer: '{answer}'. {message}"


def prompt_for_password(confirm: bool = True, allow_empty: bool = False) -> str:
    """Prompt user for a password with optional confirmation."""
    while True:
        pwd1 = prompt_for_string("Enter password:", hide=True, allow_empty=allow_empty)
        if confirm:
            pwd2 = prompt_for_string("Re-enter password:", hide=True, allow_empty=allow_empty)
            if pwd1 != pwd2:
                print("Passwords don't match. Please try again.")
                continue

        return pwd1


def prompt_for_selection(choices: List[Choice], message: str, allow_multiple=False) -> Union[Any, List[Any]]:
    """Prompt user to select from a list of choices."""
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
    """Prompt user for yes/no confirmation."""
    return inquirer.confirm(message=message, default=default).execute()


def deserialise_tag_value(tag: DataObject.Tag) -> DataObject.Tag:
    """Convert tag value string to appropriate Python type."""
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
    """Prompt user for multiple tags in key=value format."""
    answers = []
    while True:
        answer = prompt_for_string(message, allow_empty=True)
        if not answer:
            return answers

        elif answer.count('=') > 1:
            print("Invalid tag. Use key=value format. Must not contain more than one '=' character. Try again.")

        elif answer.count('=') == 0:
            answers.append(DataObject.Tag(key=answer))

        else:
            answer = answer.split('=')
            answers.append(deserialise_tag_value(DataObject.Tag(key=answer[0], value=answer[1])))


def prompt_if_missing(args: dict, key: str, function, **func_args) -> Any:
    """Call prompt function if key is missing from args."""
    if args.get(key, None) is None:
        args[key] = function(**func_args)
    return args[key]


def default_if_missing(args: dict, key: str, default: Any) -> Any:
    """Set default value if key is missing from args."""
    if args.get(key, None) is None:
        args[key] = default
    return args[key]
