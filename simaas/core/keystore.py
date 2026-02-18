from __future__ import annotations

import json
import os
import string

from threading import Lock
from typing import Optional

import zmq
from pydantic import ValidationError
from zmq.utils import z85

from simaas.core.eckeypair import ECKeyPair
from simaas.core.errors import ConfigurationError, InternalError
from simaas.core.helpers import hash_json_object, get_timestamp_now
from simaas.core.keypair import KeyPair
from simaas.core.logging import Logging
from simaas.core.rsakeypair import RSAKeyPair
from simaas.core.schemas import KeystoreContent
from simaas.core.helpers import generate_random_string, write_json_to_file
from simaas.core.assets import MasterKeyPairAsset, KeyPairAsset, ContentKeysAsset, SSHCredentialsAsset, \
    GithubCredentialsAsset
from simaas.core.identity import generate_identity_token, Identity

logger = Logging.get('simaas.core')


class Keystore:
    def __init__(self, content: KeystoreContent, path: str = None, password: str = None) -> None:
        self._mutex = Lock()
        self._content = content
        self._path = path
        self._password = password

        self._loaded = {
            'master-key': MasterKeyPairAsset.load(content.assets['master-key'], password)
        }
        self._identity = None

        self._master = self._loaded['master-key'].get()

        # load all other assets
        for key, asset in content.assets.items():
            if key != 'master-key':
                if asset['type'] == KeyPairAsset.__name__:
                    self._loaded[key] = KeyPairAsset.load(asset, self._master)

                elif asset['type'] == ContentKeysAsset.__name__:
                    self._loaded[key] = ContentKeysAsset.load(asset, self._master)

                elif asset['type'] == GithubCredentialsAsset.__name__:
                    self._loaded[key] = GithubCredentialsAsset.load(asset, self._master)

                elif asset['type'] == SSHCredentialsAsset.__name__:
                    self._loaded[key] = SSHCredentialsAsset.load(asset, self._master)

        # keep references to essential keys
        self._s_key = self._loaded['signing-key'].get()
        self._e_key = self._loaded['encryption-key'].get()

        # create curve public key
        self._curve_secret: bytes = self._s_key.private_as_bytes()
        self._curve_secret: bytes = z85.encode(self._curve_secret)
        self._curve_public: bytes = zmq.curve_public(self._curve_secret)

        # check if signature is valid
        content_hash = hash_json_object(content.model_dump(), exclusions=['signature'])
        if not self._s_key.verify(content_hash, content.signature):
            raise ConfigurationError(
                path='keystore',
                expected='valid signature',
                actual='invalid signature',
                hint='The keystore file may be corrupted or tampered with'
            )

        self._update_identity()

    @classmethod
    def new(cls, name: str, email: str = None, path: str = None, password: str = None) -> Keystore:
        # create random keystore id
        iid = generate_random_string(64, characters=string.ascii_lowercase+string.digits)

        # create required assets
        master_key = MasterKeyPairAsset(RSAKeyPair.create_new())
        signing_key = KeyPairAsset(ECKeyPair.create_new())
        encryption_key = KeyPairAsset(RSAKeyPair.create_new())
        content_keys = ContentKeysAsset()
        ssh_credentials = SSHCredentialsAsset()
        github_credentials = GithubCredentialsAsset()

        # create the keystore content
        content = {
            'iid': iid,
            'profile': {
                'name': name,
                'email': email if email else 'none'
            },
            'nonce': 0,
            'assets': {
                'master-key': master_key.store(password),
                'signing-key': signing_key.store(master_key.get()),
                'encryption-key': encryption_key.store(master_key.get()),
                'content-keys': content_keys.store(master_key.get()),
                'ssh-credentials': ssh_credentials.store(master_key.get()),
                'github-credentials': github_credentials.store(master_key.get())
            }
        }

        # sign the contents of the keystore
        content_hash = hash_json_object(content)
        content['signature'] = signing_key.get().sign(content_hash)

        # update path with fully qualified name
        if path is not None:
            path = os.path.join(path, f"{iid}.json")

        keystore = Keystore(KeystoreContent.model_validate(content), path=path, password=password)
        keystore.sync()

        logger.info(f"keystore created: id={keystore.identity.id} "
                    f"s_pubkey={keystore._s_key.public_as_string()} "
                    f"e_pubkey={keystore._e_key.public_as_string()}")

        return keystore

    @classmethod
    def from_content(cls, content: KeystoreContent) -> Keystore:
        # check if we have required assets
        for required in ['master-key', 'signing-key', 'encryption-key', 'content-keys', 'ssh-credentials',
                         'github-credentials']:
            if required not in content.assets:
                raise ConfigurationError(
                    path='keystore.assets',
                    expected=f"'{required}' asset",
                    actual='missing',
                    hint='Keystore is missing required assets'
                )

        # create keystore
        keystore = Keystore(content)
        logger.info(f"keystore loaded: iid={keystore.identity.id} "
                    f"s_key={keystore._s_key.public_as_string()} "
                    f"e_key={keystore._e_key.public_as_string()}")

        return keystore

    @classmethod
    def from_file(cls, keystore_path: str, password: str) -> Keystore:
        # check if keystore file exists
        if not os.path.isfile(keystore_path):
            raise ConfigurationError(
                path=keystore_path,
                expected='keystore file',
                actual='not found',
                hint='Check if the keystore path is correct'
            )

        # load content and validate
        try:
            with open(keystore_path, 'r') as f:
                content = KeystoreContent.model_validate(json.load(f))
        except ValidationError:
            raise ConfigurationError(
                path=keystore_path,
                expected='valid JSON schema',
                actual='invalid schema',
                hint='Keystore content is not compliant with expected schema'
            )

        # check if we have required assets
        for required in ['master-key', 'signing-key', 'encryption-key', 'content-keys', 'ssh-credentials',
                         'github-credentials']:
            if required not in content.assets:
                raise ConfigurationError(
                    path='keystore.assets',
                    expected=f"'{required}' asset",
                    actual='missing',
                    hint='Keystore is missing required assets'
                )

        # create keystore
        keystore = Keystore(content, path=keystore_path, password=password)
        logger.info(f"keystore loaded: iid={keystore.identity.id} "
                    f"s_key={keystore._s_key.public_as_string()} "
                    f"e_key={keystore._e_key.public_as_string()}")

        return keystore

    @property
    def content(self) -> KeystoreContent:
        return self._content

    @property
    def path(self) -> Optional[str]:
        with self._mutex:
            return self._path

    @property
    def identity(self) -> Identity:
        with self._mutex:
            return self._identity

    @property
    def encryption_key(self) -> KeyPair:
        with self._mutex:
            return self._e_key

    @property
    def signing_key(self) -> KeyPair:
        with self._mutex:
            return self._s_key

    def curve_secret_key(self) -> bytes:
        return self._curve_secret

    def curve_public_key(self) -> bytes:
        return self._curve_public

    def update_profile(self, name: str = None, email: str = None) -> Identity:
        with self._mutex:
            if name is not None:
                self._content.profile.name = name

            if email is not None:
                self._content.profile.email = email

        if name or email:
            self.sync()

        return self._identity

    def encrypt(self, content: bytes) -> bytes:
        with self._mutex:
            return self._e_key.encrypt(content, base64_encoded=True)

    def decrypt(self, content: bytes) -> bytes:
        with self._mutex:
            return self._e_key.decrypt(content, base64_encoded=True)

    def sign(self, message: bytes) -> str:
        with self._mutex:
            return self._s_key.sign(message)

    def verify(self, message: bytes, signature: str) -> bool:
        with self._mutex:
            return self._s_key.verify(message, signature)

    @property
    def content_keys(self) -> ContentKeysAsset:
        with self._mutex:
            return self._loaded['content-keys']

    @property
    def ssh_credentials(self) -> SSHCredentialsAsset:
        with self._mutex:
            return self._loaded['ssh-credentials']

    @property
    def github_credentials(self) -> GithubCredentialsAsset:
        with self._mutex:
            return self._loaded['github-credentials']

    def _update_identity(self) -> None:
        # generate valid signature for the identity
        token = generate_identity_token(iid=self._content.iid,
                                        name=self._content.profile.name,
                                        email=self._content.profile.email,
                                        s_public_key=self._s_key.public_as_string(),
                                        e_public_key=self._e_key.public_as_string(),
                                        nonce=self._content.nonce)
        signature = self._s_key.sign(token.encode('utf-8'))

        # update the signature
        self._identity = Identity(id=self._content.iid,
                                  name=self._content.profile.name,
                                  email=self._content.profile.email,
                                  s_public_key=self._s_key.public_as_string(),
                                  e_public_key=self._e_key.public_as_string(),
                                  c_public_key=self._curve_public.hex(),
                                  nonce=self._content.nonce,
                                  signature=signature,
                                  last_seen=get_timestamp_now())

        # verify the identity's integrity
        if not self._identity.verify_integrity():
            raise InternalError(
                component='keystore',
                state='identity verification failed',
                hint='The generated identity failed integrity verification'
            )

    def sync(self) -> None:
        with self._mutex:
            # increase the nonce
            self._content.nonce += 1

            # serialise all assets
            self._content.assets = {
                key: asset.store(protection=self._password if key == 'master-key' else self._master)
                for key, asset in self._loaded.items()
            }

            # sign the contents of the keystore
            content_hash = hash_json_object(self._content.model_dump(), exclusions=['signature'])
            self._content.signature = self._s_key.sign(content_hash)

            # write contents to disk
            if self._path is not None:
                write_json_to_file(self._content.model_dump(), self._path)

            # update identity
            self._update_identity()

    def delete(self) -> None:
        with self._mutex:
            if self._path is not None:
                os.remove(self._path)
