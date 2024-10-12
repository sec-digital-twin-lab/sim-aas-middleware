from abc import abstractmethod, ABC

import cryptography.hazmat.primitives.serialization as serialization

from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.helpers import hash_bytes_object
from simaas.core.logging import Logging

logger = Logging.get('simaas.core')


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

    def private_as_bytes(self, password: str = None) -> bytes:
        """
        Serialises the private key and returns it as byte array (or None in case this KeyPair instance does not
        have a private key).
        :param password: the password to protect the private key
        :return: byte array representing the password-protected private key or None if no private key is available
        """
        if self.private_key is None:
            raise SaaSRuntimeException('No private key found')

        # encrypt with a password?
        key_encryption_algorithm = serialization.NoEncryption()
        if password:
            key_encryption_algorithm = serialization.BestAvailableEncryption(password.encode('utf-8'))

        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=key_encryption_algorithm
        )

    def public_as_bytes(self) -> bytes:
        """
        Serialises the public key and returns it as byte array.
        :return: byte array representing the public key
        """
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def private_as_string(self, password: str = None, truncate: bool = True) -> str:
        """
        Serialises the private key and returns it as string (or None in case this KeyPair instance does not
        have a private key).
        :param password: the password to protect the private key
        :param truncate: indicates whether to create a truncated string (default: False)
        :return: string representing of the private key or None if no private key is available
        """
        if self.private_key is None:
            raise SaaSRuntimeException('No private key found')

        result = self.private_as_bytes(password).decode('utf-8')
        if truncate:
            if password:
                result = result.replace('\n', '')
                result = result[37:-35]
            else:
                result = result.replace('\n', '')
                result = result[27:-25]

        return result

    def public_as_string(self, truncate: bool = True) -> str:
        """
        Serialises the public key and returns it as string. If truncate=True, the PEM prefix and suffix is removed
        as well as all white space characters.
        :param truncate: indicates whether to create a truncated string (default: True)
        :return: string representing the public key
        """
        result = self.public_as_bytes().decode('utf-8')
        if truncate:
            result = result.replace('\n', '')
            result = result[26:-24]
        return result

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
