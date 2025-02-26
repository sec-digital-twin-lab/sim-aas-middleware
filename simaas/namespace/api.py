import abc

from simaas.dor.api import DORInterface
from simaas.rti.api import RTIInterface


class Namespace(abc.ABC):
    @abc.abstractmethod
    def id(self) -> str:
        ...

    @abc.abstractmethod
    def name(self) -> str:
        ...

    @abc.abstractmethod
    def dor(self) -> DORInterface:
        ...

    @abc.abstractmethod
    def rti(self) -> RTIInterface:
        ...

    @abc.abstractmethod
    def destroy(self) -> None:
        ...
