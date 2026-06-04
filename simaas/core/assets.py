from __future__ import annotations

import datetime
import hashlib
import json
from typing import Dict, Optional, List

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import BaseModel

from simaas.core.eckeypair import ECKeyPair
from simaas.core.errors import ValidationError
from simaas.core.keypair import KeyPair
from simaas.core.rsakeypair import RSAKeyPair
from simaas.core.schemas import GithubCredentials, SSHCredentials


def _decrypt(content: str, key: KeyPair) -> str:
    return key.decrypt(content.encode('utf-8'), base64_encoded=True).decode('utf-8')


def _encrypt(content: str, key: KeyPair) -> str:
    return key.encrypt(content.encode('utf-8'), base64_encoded=True).decode('utf-8')


class MasterKeyPairAsset:
    class Content(BaseModel):
        type: str
        info: str
        pppk: str

    def __init__(self, keypair: KeyPair) -> None:
        self._keypair = keypair

    @classmethod
    def load(cls, asset: dict, password: str) -> MasterKeyPairAsset:
        asset = MasterKeyPairAsset.Content.model_validate(asset)

        # create keypair from content
        if asset.info.startswith('RSA'):
            keypair = RSAKeyPair.from_private_key_string(asset.pppk, password=password)
            return MasterKeyPairAsset(keypair)

        elif asset.info.startswith('EC'):
            keypair = ECKeyPair.from_private_key_string(asset.pppk, password=password)
            return MasterKeyPairAsset(keypair)

        else:
            raise ValidationError(
                field='asset.info',
                expected='RSA or EC key type',
                actual=asset.info,
                hint='Unrecognised key type in asset'
            )

    def get(self) -> KeyPair:
        return self._keypair

    def store(self, protection: str) -> dict:
        return {
            'type': MasterKeyPairAsset.__name__,
            'info': self._keypair.info(),
            'pppk': self._keypair.private_as_string(password=protection)
        }


class KeyPairAsset:
    class Content(BaseModel):
        type: str
        info: str
        private_key: str

    def __init__(self, keypair: KeyPair) -> None:
        self._keypair = keypair

    @classmethod
    def load(cls, asset: dict, master: KeyPair) -> KeyPairAsset:
        asset = KeyPairAsset.Content.model_validate(asset)

        # create keypair from content
        if asset.info.startswith('RSA'):
            keypair = RSAKeyPair.from_private_key_string(_decrypt(asset.private_key, master))
            return KeyPairAsset(keypair)

        elif asset.info.startswith('EC'):
            keypair = ECKeyPair.from_private_key_string(_decrypt(asset.private_key, master))
            return KeyPairAsset(keypair)

        else:
            raise ValidationError(
                field='asset.info',
                expected='RSA or EC keypair type',
                actual=asset.info,
                hint='Unrecognised keypair type in asset'
            )

    def get(self) -> KeyPair:
        return self._keypair

    def store(self, protection: KeyPair) -> dict:
        return {
            'type': KeyPairAsset.__name__,
            'info': self._keypair.info(),
            'private_key': _encrypt(self._keypair.private_as_string(), protection)
        }


class ContentKeysAsset:
    class Content(BaseModel):
        type: str
        content_keys: str

    def __init__(self, content_keys: dict = None) -> None:
        self._content_keys = content_keys if content_keys else {}

    @classmethod
    def load(cls, asset: dict, master: KeyPair) -> ContentKeysAsset:
        asset = ContentKeysAsset.Content.model_validate(asset)

        keys = json.loads(_decrypt(asset.content_keys, master))
        return ContentKeysAsset(keys)

    def update(self, obj_id: str, content_key: str) -> None:
        self._content_keys[obj_id] = content_key

    def get(self, obj_id: str) -> str:
        return self._content_keys.get(obj_id)

    def store(self, protection: KeyPair) -> dict:
        return {
            'type': ContentKeysAsset.__name__,
            'content_keys': _encrypt(json.dumps(self._content_keys), protection)
        }


class GithubCredentialsAsset:
    class Content(BaseModel):
        type: str
        credentials: str

    def __init__(self, credentials: Dict[str, GithubCredentials] = None):
        self._credentials = credentials if credentials else {}

    @classmethod
    def load(cls, asset: dict, master: KeyPair) -> GithubCredentialsAsset:
        asset = GithubCredentialsAsset.Content.model_validate(asset)

        credentials = json.loads(_decrypt(asset.credentials, master))
        credentials = {key: GithubCredentials.model_validate(c) for key, c in credentials.items()}
        return GithubCredentialsAsset(credentials)

    def store(self, protection: KeyPair) -> dict:
        credentials = {key: c.model_dump() for key, c in self._credentials.items()}
        return {
            'type': GithubCredentialsAsset.__name__,
            'credentials': _encrypt(json.dumps(credentials), protection)
        }

    def list(self) -> List[str]:
        return list(self._credentials.keys())

    def get(self, name: str) -> Optional[GithubCredentials]:
        return self._credentials.get(name, None)

    def update(self, name: str, credentials: GithubCredentials) -> None:
        self._credentials[name] = credentials

    def remove(self, name: str) -> Optional[GithubCredentials]:
        return self._credentials.pop(name, None)


class TLSCertAsset:
    class Content(BaseModel):
        type: str
        cert_pem: str
        key_pem: str

    def __init__(self, cert_pem: bytes, key_pem: bytes) -> None:
        self._cert_pem = cert_pem
        self._key_pem = key_pem

    @classmethod
    def create_new(cls) -> TLSCertAsset:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'simaas-node')])
        not_before = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        not_after = datetime.datetime(2099, 1, 1, tzinfo=datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(not_before)
                .not_valid_after(not_after)
                .sign(key, hashes.SHA256()))

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return TLSCertAsset(cert_pem, key_pem)

    @classmethod
    def load(cls, asset: dict, master: KeyPair) -> TLSCertAsset:
        asset = TLSCertAsset.Content.model_validate(asset)
        cert_pem = asset.cert_pem.encode('utf-8')
        key_pem = _decrypt(asset.key_pem, master).encode('utf-8')
        return TLSCertAsset(cert_pem, key_pem)

    def store(self, protection: KeyPair) -> dict:
        return {
            'type': TLSCertAsset.__name__,
            'cert_pem': self._cert_pem.decode('utf-8'),
            'key_pem': _encrypt(self._key_pem.decode('utf-8'), protection),
        }

    def cert_pem(self) -> bytes:
        return self._cert_pem

    def key_pem(self) -> bytes:
        return self._key_pem

    def spki_hex(self) -> str:
        cert = x509.load_pem_x509_certificate(self._cert_pem)
        spki = cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(spki).hexdigest()


class SSHCredentialsAsset:
    class Content(BaseModel):
        type: str
        credentials: str

    def __init__(self, credentials: Dict[str, SSHCredentials] = None):
        self._credentials = credentials if credentials else {}

    @classmethod
    def load(cls, asset: dict, master: KeyPair) -> SSHCredentialsAsset:
        asset = SSHCredentialsAsset.Content.model_validate(asset)

        credentials = json.loads(_decrypt(asset.credentials, master))
        credentials = {key: SSHCredentials.model_validate(c) for key, c in credentials.items()}
        return SSHCredentialsAsset(credentials)

    def store(self, protection: KeyPair) -> dict:
        credentials = {key: c.model_dump() for key, c in self._credentials.items()}
        return {
            'type': SSHCredentialsAsset.__name__,
            'credentials': _encrypt(json.dumps(credentials), protection)
        }

    def list(self) -> List[str]:
        return list(self._credentials.keys())

    def get(self, name: str) -> Optional[SSHCredentials]:
        return self._credentials.get(name, None)

    def update(self, name: str, credentials: SSHCredentials) -> None:
        self._credentials[name] = credentials

    def remove(self, name: str) -> Optional[SSHCredentials]:
        return self._credentials.pop(name, None)
