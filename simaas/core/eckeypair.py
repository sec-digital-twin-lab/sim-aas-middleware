from __future__ import annotations

from typing import Optional

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from simaas.core.keypair import KeyPair
from simaas.core.logging import Logging

logger = Logging.get('simaas.core')


class ECKeyPair(KeyPair):
    """
    ECKeyPair encapsulates the functionality for Elliptic Curve (EC) key pairs. It provides a number of convenience
    methods to create a ECKeyPair instance as well as for (de)serialisation of keys. A EC key pair consists of a
    private key and a public key whereby the public key can be derived from the private key. ECKeyPair provides also
    a number of methods for creating and verifying signatures and authentication/authorisation tokens.
    """

    def __init__(self, private_key: Optional[Ed25519PrivateKey], public_key: Ed25519PublicKey) -> None:
        KeyPair.__init__(self, private_key, public_key)

    def info(self) -> str:
        return f"EC/Ed25519/256/{self.iid}"

    @classmethod
    def create_new(cls) -> ECKeyPair:
        """
        Creates an ECKeyPair instance with a randomly generated private key.
        :return: ECKeyPair instance
        """
        private_key = ed25519.Ed25519PrivateKey.generate()
        return ECKeyPair.from_private_key(private_key)

    @classmethod
    def from_private_key(cls, private_key: Ed25519PrivateKey) -> ECKeyPair:
        """
        Creates an ECKeyPair instance based on a given private key.
        :param private_key:
        :return: ECKeyPair instance
        """
        public_key = private_key.public_key()
        return ECKeyPair(private_key, public_key)

    @classmethod
    def from_private_key_string(cls, private_key_string: str, password: str = None) -> ECKeyPair:
        """
        Creates an ECKeyPair instance based on a given private key string.
        :param private_key_string:
        :param password: the password used to protect the private key
        :return: ECKeyPair instance
        """
        private_key_bytes = bytes.fromhex(private_key_string)
        return ECKeyPair.from_private_key(Ed25519PrivateKey.from_private_bytes(private_key_bytes))

    @classmethod
    def from_private_key_file(cls, path: str, password: str) -> ECKeyPair:
        """
        Creates an ECKeyPair instance by reading a private key from a PEM file.
        :param path: the path to the file containing the private key
        :param password: the password used to protect the private key
        :return: ECKeyPair instance
        """
        with open(path, "rb") as f:
            private_key_bytes = f.read()
            return ECKeyPair.from_private_key(Ed25519PrivateKey.from_private_bytes(private_key_bytes))

    @classmethod
    def from_public_key(cls, public_key: Ed25519PublicKey) -> ECKeyPair:
        """
        Creates an ECKeyPair instance based on a given public key. Note that the private key cannot be derived
        from the public key. An ECKeyPair instance generated this way cannot be used for creating signatures, only
        for verification.
        :param public_key: the public key
        :return: ECKeyPair instance
        """
        return ECKeyPair(None, public_key)

    @classmethod
    def from_public_key_bytes(cls, public_key_bytes: bytes) -> ECKeyPair:
        """
        Creates an ECKeyPair instance based on a given public key presented as byte array. Note that the private key
        cannot be derived from the public key. An ECKeyPair instance generated this way cannot be used for creating
        signatures, only for verification.
        :param public_key_bytes: the public key as byte array
        :return: ECKeyPair instance
        """
        return ECKeyPair.from_public_key(Ed25519PublicKey.from_public_bytes(public_key_bytes))

    @classmethod
    def from_public_key_string(cls, public_key_string: str) -> ECKeyPair:
        """
        Creates an ECKeyPair instance based on a given public key presented as string. Note that the private key
        cannot be derived from the public key. An ECKeyPair instance generated this way cannot be used for creating
        signatures, only for verification.
        :param public_key_string: the public key as string (full-length or truncated)
        :return: ECKeyPair instance
        """
        public_key_bytes = bytes.fromhex(public_key_string)
        return ECKeyPair.from_public_key_bytes(public_key_bytes)

    @classmethod
    def from_public_key_file(cls, path: str) -> ECKeyPair:
        """
        Creates an ECKeyPair instance by reading a public key from a PEM file. Public keys are not password protected,
        so password is required. An ECKeyPair instance generated this way cannot be used for creating signatures, only
        for verification.
        :param path: the path to the file containing the public key
        :return: ECKeyPair instance
        """
        with open(path, "rb") as f:
            public_key_bytes = f.read()
            return ECKeyPair.from_public_key_bytes(public_key_bytes)

    def private_as_bytes(self, password: str = None) -> bytes:
        return self.private_key.private_bytes_raw()

    def private_as_string(self, password: str = None, truncate: bool = True) -> str:
        return self.private_as_bytes().hex()

    def public_as_bytes(self) -> bytes:
        return self.public_key.public_bytes_raw()

    def public_as_string(self, truncate: bool = True) -> str:
        return self.public_as_bytes().hex()

    def sign(self, message: bytes) -> str:
        """
        Sign a message using the private key.
        :param message: the message that has to be signed
        :return: the signature
        """
        return self.private_key.sign(message).hex()

    def verify(self, message: bytes, signature: str) -> bool:
        """
        Verifies the signature for a given message using the public key.
        :param message: the message that has been used for signing
        :param signature: the signature
        :return: True of False depending on whether the signature is valid
        """
        try:
            self.public_key.verify(bytes.fromhex(signature), message)
            return True
        except InvalidSignature:
            return False

    def encrypt(self, message: bytes, base64_encoded: bool = False) -> bytes:
        raise NotImplementedError("EC keys do not currently support encryption/decryption")

    def decrypt(self, message: bytes, base64_encoded: bool = False) -> bytes:
        raise NotImplementedError("EC keys do not currently support encryption/decryption")
