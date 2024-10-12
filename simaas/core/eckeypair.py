from __future__ import annotations

from typing import Optional

import cryptography.hazmat.primitives.serialization as serialization
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey, EllipticCurvePublicKey

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from simaas.core.exceptions import SaaSRuntimeException
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

    def __init__(self, private_key: Optional[EllipticCurvePrivateKey], public_key: EllipticCurvePublicKey) -> None:
        KeyPair.__init__(self, private_key, public_key)

    def info(self) -> str:
        return f"EC/{self.private_key.curve.name}/{self.private_key.curve.key_size}/{self.iid}"

    @classmethod
    def create_new(cls) -> ECKeyPair:
        """
        Creates an ECKeyPair instance with a randomly generated private key.
        :return: ECKeyPair instance
        """
        private_key = ec.generate_private_key(
            curve=ec.SECP384R1(),
            backend=default_backend()
        )
        return ECKeyPair.from_private_key(private_key)

    @classmethod
    def from_private_key(cls, private_key: EllipticCurvePrivateKey) -> ECKeyPair:
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
        if password:
            password = password.encode('utf-8')

            if '-----BEGIN ENCRYPTED PRIVATE KEY-----' not in private_key_string:
                private_key_string = \
                    '\n'.join(private_key_string[i:i + 64] for i in range(0, len(private_key_string), 64))
                private_key_string = \
                    f"-----BEGIN ENCRYPTED PRIVATE KEY-----\n{private_key_string}\n-----END ENCRYPTED PRIVATE KEY-----"

        else:
            if '-----BEGIN PRIVATE KEY-----' not in private_key_string:
                private_key_string = \
                    '\n'.join(private_key_string[i:i + 64] for i in range(0, len(private_key_string), 64))
                private_key_string = f"-----BEGIN PRIVATE KEY-----\n{private_key_string}\n-----END PRIVATE KEY-----"

        try:
            private_key = serialization.load_pem_private_key(
                data=private_key_string.encode('utf-8'),
                password=password,
                backend=default_backend()
            )
            public_key = private_key.public_key()
            return ECKeyPair(private_key, public_key)

        except Exception:
            raise SaaSRuntimeException("Loading key failed. Password wrong?")

    @classmethod
    def from_private_key_file(cls, path: str, password: str) -> ECKeyPair:
        """
        Creates an ECKeyPair instance by reading a private key from a PEM file.
        :param path: the path to the file containing the private key
        :param password: the password used to protect the private key
        :return: ECKeyPair instance
        """
        with open(path, "rb") as f:
            private_key = serialization.load_pem_private_key(
                data=f.read(),
                password=password.encode('utf-8'),
                backend=default_backend()
            )
            return ECKeyPair.from_private_key(private_key)

    @classmethod
    def from_public_key(cls, public_key: EllipticCurvePublicKey) -> ECKeyPair:
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
        public_key = serialization.load_pem_public_key(
            data=public_key_bytes,
            backend=default_backend()
        )
        return ECKeyPair.from_public_key(public_key)

    @classmethod
    def from_public_key_string(cls, public_key_string: str) -> ECKeyPair:
        """
        Creates an ECKeyPair instance based on a given public key presented as string. Note that the private key
        cannot be derived from the public key. An ECKeyPair instance generated this way cannot be used for creating
        signatures, only for verification.
        :param public_key_string: the public key as string (full-length or truncated)
        :return: ECKeyPair instance
        """
        if '-----BEGIN PUBLIC KEY-----' not in public_key_string:
            public_key_string = '\n'.join(public_key_string[i:i + 64] for i in range(0, len(public_key_string), 64))
            public_key_string = f"-----BEGIN PUBLIC KEY-----\n{public_key_string}\n-----END PUBLIC KEY-----"

        return ECKeyPair.from_public_key_bytes(public_key_string.encode('utf-8'))

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
            public_key = serialization.load_pem_public_key(
                data=f.read(),
                backend=default_backend()
            )
            return ECKeyPair.from_public_key(public_key)

    def sign(self, message: bytes) -> str:
        """
        Sign a message using the private key.
        :param message: the message that has to be signed
        :return: the signature
        """
        return self.private_key.sign(message, ec.ECDSA(hashes.SHA256())).hex()

    def verify(self, message: bytes, signature: str) -> bool:
        """
        Verifies the signature for a given message using the public key.
        :param message: the message that has been used for signing
        :param signature: the signature
        :return: True of False depending on whether the signature is valid
        """
        try:
            self.public_key.verify(bytes.fromhex(signature), message, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False

    def encrypt(self, message: bytes, base64_encoded: bool = False) -> bytes:
        raise NotImplementedError("EC keys do not currently support encryption/decryption")

    def decrypt(self, message: bytes, base64_encoded: bool = False) -> bytes:
        raise NotImplementedError("EC keys do not currently support encryption/decryption")
