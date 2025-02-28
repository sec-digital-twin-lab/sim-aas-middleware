import abc

from simaas.core.keystore import Keystore

from simaas.dor.api import DORInterface
from simaas.rti.api import RTIInterface


class Namespace(abc.ABC):
    def __init__(self, dor: DORInterface, rti: RTIInterface):
        self._dor = dor
        self._rti = rti

    @abc.abstractmethod
    def id(self) -> str:
        ...

    @abc.abstractmethod
    def name(self) -> str:
        ...

    @abc.abstractmethod
    def keystore(self) -> Keystore:
        ...

    @property
    def dor(self) -> DORInterface:
        return self._dor

    @property
    def rti(self) -> RTIInterface:
        return self._rti

    @abc.abstractmethod
    def destroy(self) -> None:
        ...
