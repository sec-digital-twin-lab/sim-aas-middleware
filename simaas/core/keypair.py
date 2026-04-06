from abc import abstractmethod, ABC

from simaas.core.helpers import hash_bytes_object
from simaas.core.logging import get_logger

log = get_logger('simaas.core', 'core')


class KeyPair(ABC):
    """
    KeyPair encapsulates the functionality for asymmetric key pairs. It provides a number of convenience methods for
    (de)serialisation of keys. A key pair consists of a private key and a public key whereby the public key can be
    derived from the private key.
    """

    def __init__(self, private_key, public_key):
        self.private_key = private_key
        self.public_key = public_key
        self.iid = hash_bytes_object(self.public_as_bytes()).hex()
        self.short_iid = f"{self.iid[:4]}...{self.iid[-4:]}"

    @abstractmethod
    def info(self) -> str:
        pass

    @abstractmethod
    def private_as_bytes(self, password: str = None) -> bytes:
        pass

    @abstractmethod
    def public_as_bytes(self) -> bytes:
        pass

    @abstractmethod
    def private_as_string(self, password: str = None, truncate: bool = True) -> str:
        pass

    @abstractmethod
    def public_as_string(self, truncate: bool = True) -> str:
        pass

    def write_private(self, path: str, password: str) -> None:
        """
        Writes the private key into a file.
        :param path: the path where to store the private key
        :param password: the password used to protect the private key
        :return: None
        """
        with open(path, 'wb') as f:
            f.write(self.private_as_bytes(password))

    def write_public(self, path: str) -> None:
        """
        Writes the public key into a file.
        :param path: the path where to store the public key
        :return: None
        """
        with open(path, 'wb') as f:
            f.write(self.public_as_bytes())

    @abstractmethod
    def sign(self, message: bytes) -> str:
        pass

    @abstractmethod
    def verify(self, message: bytes, signature: str) -> bool:
        pass

    @abstractmethod
    def encrypt(self, message: bytes, base64_encoded: bool = False) -> bytes:
        pass

    @abstractmethod
    def decrypt(self, message: bytes, base64_encoded: bool = False) -> bytes:
        pass
