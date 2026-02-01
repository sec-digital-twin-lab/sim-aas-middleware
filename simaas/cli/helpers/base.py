"""Base CLI command classes and argument handling."""

from __future__ import annotations

import argparse
import logging
import sys
from abc import abstractmethod, ABC
from argparse import ArgumentParser
from typing import Optional

from simaas.core.logging import get_logger, initialise as logging_initialise


log = get_logger('simaas.cli', 'cli')


class Argument:
    """Wrapper for argparse argument definitions."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


class CLIExecutable(ABC):
    """Abstract base class for CLI executables."""

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
    """Base class for CLI commands."""

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
    """Base class for CLI command groups."""

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
    """Custom argument parser that prints help on error."""

    def error(self, message: str) -> None:
        self.print_help()
        sys.exit(1)


class CLIParser(CLICommandGroup):
    """Main CLI parser that handles initialization and execution."""

    def __init__(self, description, arguments: list[Argument] = None, commands: list[CLIExecutable] = None) -> None:
        super().__init__('main', description, arguments, commands)

    def execute(self, args: list) -> None:
        from simaas.cli.helpers.storage import initialise_storage_folder

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
            logging_initialise(level=level, log_path=log_path, console_log_enabled=console_enabled)
            super().execute(args)

        except argparse.ArgumentError:
            parser.print_help()
