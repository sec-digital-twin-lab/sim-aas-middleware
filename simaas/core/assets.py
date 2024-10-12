from __future__ import annotations

import json
from typing import Dict, Optional, List

from pydantic import BaseModel

from simaas.core.eckeypair import ECKeyPair
from simaas.core.exceptions import SaaSRuntimeException
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
        asset = MasterKeyPairAsset.Content.parse_obj(asset)

        # create keypair from content
        if asset.info.startswith('RSA'):
            keypair = RSAKeyPair.from_private_key_string(asset.pppk, password=password)
            return MasterKeyPairAsset(keypair)

        elif asset.info.startswith('EC'):
            keypair = ECKeyPair.from_private_key_string(asset.pppk, password=password)
            return MasterKeyPairAsset(keypair)

        else:
            raise SaaSRuntimeException(f"Unrecognised key type '{asset.info}'")

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
        asset = KeyPairAsset.Content.parse_obj(asset)

        # create keypair from content
        if asset.info.startswith('RSA'):
            keypair = RSAKeyPair.from_private_key_string(_decrypt(asset.private_key, master))
            return KeyPairAsset(keypair)

        elif asset.info.startswith('EC'):
            keypair = ECKeyPair.from_private_key_string(_decrypt(asset.private_key, master))
            return KeyPairAsset(keypair)

        else:
            raise SaaSRuntimeException(f"Unrecognised keypair type '{asset.info}'")

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
        asset = ContentKeysAsset.Content.parse_obj(asset)

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
        asset = GithubCredentialsAsset.Content.parse_obj(asset)

        credentials = json.loads(_decrypt(asset.credentials, master))
        credentials = {key: GithubCredentials.parse_obj(c) for key, c in credentials.items()}
        return GithubCredentialsAsset(credentials)

    def store(self, protection: KeyPair) -> dict:
        credentials = {key: c.dict() for key, c in self._credentials.items()}
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


class SSHCredentialsAsset:
    class Content(BaseModel):
        type: str
        credentials: str

    def __init__(self, credentials: Dict[str, SSHCredentials] = None):
        self._credentials = credentials if credentials else {}

    @classmethod
    def load(cls, asset: dict, master: KeyPair) -> SSHCredentialsAsset:
        asset = SSHCredentialsAsset.Content.parse_obj(asset)

        credentials = json.loads(_decrypt(asset.credentials, master))
        credentials = {key: SSHCredentials.parse_obj(c) for key, c in credentials.items()}
        return SSHCredentialsAsset(credentials)

    def store(self, protection: KeyPair) -> dict:
        credentials = {key: c.dict() for key, c in self._credentials.items()}
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
