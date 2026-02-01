"""Tests for CLI DOR commands."""

import json
import os
import tempfile

import pytest

from simaas.cli.cmd_dor import DORAdd, DORMeta, DORDownload, DORRemove, DORSearch, DORTag, DORUntag, DORAccessShow, \
    DORAccessGrant, DORAccessRevoke
from simaas.cli.cmd_identity import IdentityCreate
from simaas.cli.cmd_network import NetworkList
from simaas.core.errors import CLIError
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger
from simaas.dor.schemas import DataObject

log = get_logger(__name__, 'test')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir



def test_cli_network_show(session_node, temp_dir):
    """Test CLI network list command."""
    address = session_node.rest.address()

    # get network information
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = NetworkList()
        result = cmd.execute(args)
        assert result is not None
        assert 'network' in result
        assert len(result['network']) == 2  # session node is part of a 2-node network

    except CLIError:
        assert False



def test_cli_dor_lifecycle(session_node, temp_dir):
    """Test CLI DOR data object management workflow."""
    address = session_node.rest.address()

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIError:
        assert False

    # add a data object
    try:
        file_path = os.path.join(temp_dir, 'test.json')
        with open(file_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({
                'test': 1
            }, f, indent=2)

        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'restrict_access': False,
            'content_encrypted': False,
            'assume_creator': True,
            'data-type': 'JSON',
            'data-format': 'json',
            'file': [file_path]
        }

        cmd = DORAdd()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None
        obj: DataObject = result['obj']

    except CLIError:
        assert False

    # get data object meta information
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORMeta()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None

    except CLIError:
        assert False

    # download data object content
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'destination': temp_dir,
            'obj-ids': [obj.obj_id],
        }

        cmd = DORDownload()
        result = cmd.execute(args)
        assert result is not None
        assert len(result) == 1
        assert obj.obj_id in result
        assert os.path.isfile(result[obj.obj_id])

    except CLIError:
        assert False

    # tag the data object
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id,
            'tags': ['aaa=1', 'bbb=2']
        }

        cmd = DORTag()
        result = cmd.execute(args)
        assert result is not None
        assert obj.obj_id in result
        assert 'aaa' in result[obj.obj_id].tags
        assert 'bbb' in result[obj.obj_id].tags

    except CLIError:
        assert False

    # search for data object
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'own': None,
            'data-type': None,
            'data-format': None,
            'pattern': ['aaa']
        }

        cmd = DORSearch()
        result = cmd.execute(args)
        assert result is not None
        assert len(result) == 1
        assert obj.obj_id in result

    except CLIError:
        assert False

    # untag the data object
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id,
            'keys': ['aaa']
        }

        cmd = DORUntag()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None
        assert result['obj'].obj_id == obj.obj_id
        assert 'aaa' not in result['obj'].tags
        assert 'bbb' in result['obj'].tags

    except CLIError:
        assert False

    # search for data object
    try:
        args = {
            'address': f"{address[0]}:{address[1]}",
            'own': None,
            'data-type': None,
            'data-format': None,
            'pattern': ['aaa']
        }

        cmd = DORSearch()
        result = cmd.execute(args)
        assert result is not None
        assert len(result) == 0

    except CLIError:
        assert False

    # remove data object
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-ids': [obj.obj_id],
            'confirm': True
        }

        cmd = DORRemove()
        result = cmd.execute(args)
        assert result is not None
        assert 'removed' in result
        assert result['removed'] is not None
        assert obj.obj_id in result['removed']

    except CLIError:
        assert False



def test_cli_dor_grant_show_revoke(session_node, temp_dir):
    """Test CLI DOR access control workflow (grant, show, revoke)."""
    address = session_node.rest.address()

    # create an identity
    try:
        args = {
            'keystore': temp_dir,
            'name': 'name',
            'email': 'email',
            'password': 'password'
        }

        cmd = IdentityCreate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        keystore_path = os.path.join(temp_dir, f'{keystore.identity.id}.json')
        assert os.path.isfile(keystore_path)

    except CLIError:
        assert False

    # add a data object
    try:
        file_path = os.path.join(temp_dir, 'test.json')
        with open(file_path, 'w') as f:
            # noinspection PyTypeChecker
            json.dump({
                'test': 1
            }, f, indent=2)

        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'restrict_access': False,
            'content_encrypted': False,
            'assume_creator': True,
            'data-type': 'JSON',
            'data-format': 'json',
            'file': [file_path]
        }

        cmd = DORAdd()
        result = cmd.execute(args)
        assert result is not None
        assert 'obj' in result
        assert result['obj'] is not None
        obj: DataObject = result['obj']

    except CLIError:
        assert False

    # show the access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORAccessShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'access' in result
        assert len(result['access']) == 1
        assert keystore.identity.id in result['access']

    except CLIError:
        assert False

    # revoke access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id,
            'iids': [keystore.identity.id]
        }

        cmd = DORAccessRevoke()
        result = cmd.execute(args)
        assert result is not None
        assert 'revoked' in result
        assert len(result['revoked']) == 1

    except CLIError:
        assert False

    # show the access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORAccessShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'access' in result
        assert len(result['access']) == 0

    except CLIError:
        assert False

    # grant access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'iid': keystore.identity.id,
            'obj-ids': [obj.obj_id],
        }

        cmd = DORAccessGrant()
        result = cmd.execute(args)
        assert result is not None
        assert 'granted' in result
        assert len(result['granted']) == 1

    except CLIError:
        assert False

    # show the access
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}",
            'obj-id': obj.obj_id
        }

        cmd = DORAccessShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'access' in result
        assert len(result['access']) == 1
        assert keystore.identity.id in result['access']

    except CLIError:
        assert False


