import abc
import inspect
import logging
import os
import sys
import threading
from importlib.util import spec_from_file_location, module_from_spec

from typing import Dict

from saas.core.exceptions import ExceptionContent
from saas.core.helpers import generate_random_string
from saas.core.logging import Logging
from saas.dor.schemas import ProcessorDescriptor
from saas.rti.schemas import Severity

logger = Logging.get(__name__)


class ProcessorRuntimeError(Exception):
    """
    A base class for all processor-related runtime exceptions. All exception defined by processors should inherit
    from this class.
    """

    def __init__(self, reason: str, details: dict = None, ex_id: int = None):
        """
        Initialises the exception object.
        :param reason: human-readable error message.
        :param details: a dict with details about the exception. Content must be JSON serialiseable.
        :param ex_id: (optional) a unique exception id. If not provided, it will be generated automatically.
        """
        self._content = ExceptionContent(id=ex_id if ex_id else generate_random_string(16),
                                         reason=reason,
                                         details=details)

    @property
    def id(self):
        """
        Returns the id of this exception.
        :return:
        """
        return self._content.id

    @property
    def reason(self):
        """
        Returns the human-readable reason for this exception.
        :return:
        """
        return self._content.reason

    @property
    def details(self):
        """
        Returns a dict of details about the exception.
        :return:
        """
        return self._content.details

    @property
    def content(self) -> ExceptionContent:
        """
        Returns the complete content of the exception.
        :return:
        """
        return self._content


class ProgressListener(abc.ABC):
    @abc.abstractmethod
    def on_progress_update(self, progress: float) -> None:
        """
        Callback to inform the RTI about the progress of the processor.

        :param progress: a value between 0.0 and 1.0, indicating the progress made by the processor on its current job.
        :return:
        """
        pass

    @abc.abstractmethod
    def on_output_available(self, output_name: str) -> None:
        """
        Callback to inform the RTI that a specific output is now available. Depending on the implementation, the
        RTI may attempt to fetch the output object to store it in the respective data object repository. A processor
        must use this callback to inform the RTI about all output object as soon as they become available.

        :param output_name: the name of the output (as specified in descriptor.json).
        :return:
        """
        pass

    @abc.abstractmethod
    def on_message(self, severity: Severity, message: str) -> None:
        """
        Callback to pass messages to the RTI. Messages have severity levels. It is up to the RTI implementation to
        decided what to do with the message. For example, the RTI may forward messages to an application that
        displays the message to a user.

        :param severity: the severity level of the message: DEBUG, INFO, WARNING, ERROR.
        :param message: the message content.
        :return:
        """
        pass


class ProcessorBase(abc.ABC):
    """
    An abstract base class representing a generic processor. All model adapter implementation must inherit from this
     base class. Subclasses must implement the run and interrupt methods, to carry out a simulation run and to
     interrupt it accordingly. Processor implementation must not provision for concurrency, i.e., the implementation
     should only process a single job at a time. Concurrency (if and as required) will be handled on the level of
     the RTI.
    """

    def __init__(self, proc_path: str) -> None:
        """
        Initialises the ProcessorBase with a given processor path. Note, the location indicated by the processor
        path must contain its implementation. More specifically, this includes the descriptor.json file with the
        specification of the I/O interface of the processor as well as the Python implementation (processor.py) and
        the Dockerfile.
        :param proc_path: the path to the processor implementation.
        """
        self._mutex = threading.Lock()
        self._proc_path = proc_path
        self._descriptor = ProcessorDescriptor.parse_file(os.path.join(proc_path, 'descriptor.json'))

    @property
    def path(self) -> str:
        """
        Returns the absolute path of the location of the processor implementation.
        :return:
        """
        return self._proc_path

    @property
    def name(self) -> str:
        """
        Returns the name of the processor.
        :return:
        """
        return self._descriptor.name

    @property
    def descriptor(self) -> ProcessorDescriptor:
        """
        Returns the descriptor object containing metadata about the processor.

        :return:
        """
        return self._descriptor

    @abc.abstractmethod
    def run(self, wd_path: str, listener: ProgressListener, logger: logging.Logger) -> None:
        """
        Abstract method to run the processor. Must be implemented by subclasses.

        :param wd_path: The working directory path. This directory will be prepared by the RTI to contain all
            inputs (as specified by descriptor.json) before run() is called. A processor must store all outputs (as
            specified by descriptor.json) in this directory as soon as they become available. Note: the filenames of
            inputs and outputs must exactly match the specification in descriptor.json. The RTI is responsible to
            ensure the correct naming of inputs. Similarly, the processor implementation needs to ensure the correct
            naming of outputs.
        :param listener: A callback that allows the processor to provide updates on its progress, available
            outputs and messages.
        :param logger: A logger for logging messages.
        :return:
        """
        pass

    @abc.abstractmethod
    def interrupt(self) -> None:
        """
        Abstract method to interrupt the processor. Must be implemented by subclasses.
        :return:
        """
        pass


def find_processors(search_path: str) -> Dict[str, ProcessorBase]:
    """
    Convenient function that finds all processor implementations in a given search path (including all its
    subdirectories) and returns a dictionary with the findings.

    :param search_path: the path to search for processors.
    :return: a dictionary that maps name of the processor to its implementation class.
    """
    sys.path.append(search_path)

    result = {}
    for root, dirs, files in os.walk(search_path):
        for file in files:
            if file == "processor.py":
                module_path = os.path.join(root, file)
                module_name = os.path.splitext(os.path.basename(module_path))[0]

                spec = spec_from_file_location(module_name, module_path)
                module = module_from_spec(spec)

                try:
                    spec.loader.exec_module(module)
                except Exception:
                    continue

                # module = importlib.import_module(module_name)
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and obj != ProcessorBase and issubclass(obj, ProcessorBase):
                        try:
                            instance: ProcessorBase = obj(root)
                            result[instance.name] = instance
                        except Exception as e:
                            logger.warning(f"creating instance of {obj} failed: {e}")

    return result
