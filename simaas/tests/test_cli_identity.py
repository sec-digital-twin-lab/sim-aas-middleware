import os
import tempfile

import pytest



from simaas.cli.cmd_identity import IdentityCreate, IdentityList, IdentityRemove, IdentityShow, IdentityDiscover, \
    IdentityPublish, IdentityUpdate, CredentialsList, CredentialsAddGithubCredentials, CredentialsRemove
from simaas.core.errors import CLIError
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import get_logger

log = get_logger(__name__, 'test')
repo_root_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
examples_path = os.path.join(repo_root_path, 'examples')


@pytest.fixture(scope="session")
def temp_dir():
    with tempfile.TemporaryDirectory() as tempdir:
        yield tempdir



def test_cli_identity_crud(temp_dir):
    """Test CLI identity management workflow (list, create, show, remove)."""
    # list all identities
    try:
        args = {
            'keystore': temp_dir,
        }

        cmd = IdentityList()
        result = cmd.execute(args)
        assert result is not None
        assert 'available' in result
        assert len(result['available']) == 0

    except CLIError:
        assert False

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

    # list all identities
    try:
        args = {
            'keystore': temp_dir,
        }

        cmd = IdentityList()
        result = cmd.execute(args)
        assert result is not None
        assert 'available' in result
        assert len(result['available']) == 1

    except CLIError:
        assert False

    # show identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id
        }

        cmd = IdentityShow()
        result = cmd.execute(args)
        assert result is not None
        assert 'content' in result
        assert result['content'] is not None

    except CLIError:
        assert False

    # remove the identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'confirm': True
        }

        cmd = IdentityRemove()
        result = cmd.execute(args)
        assert result is None

    except CLIError:
        assert False

    # list all identities
    try:
        args = {
            'keystore': temp_dir,
        }

        cmd = IdentityList()
        result = cmd.execute(args)
        assert result is not None
        assert 'available' in result
        assert len(result['available']) == 0

    except CLIError:
        assert False



def test_cli_identity_network(session_node, temp_dir):
    """Test CLI identity discovery, publishing, and update workflow."""
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

    # discover all identities known to the node
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityDiscover()
        result = cmd.execute(args)
        assert result is not None
        assert keystore.identity.id not in result

    except CLIError:
        assert False

    # publish identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityPublish()
        result = cmd.execute(args)
        assert result is None

    except CLIError:
        assert False

    # discover all identities known to the node
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityDiscover()
        result = cmd.execute(args)
        assert result is not None
        assert keystore.identity.id in result

    except CLIError:
        assert False

    # update identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'name': 'updated_name'
        }

        cmd = IdentityUpdate()
        result = cmd.execute(args)
        assert result is not None
        assert 'keystore' in result

        keystore: Keystore = result['keystore']
        assert keystore.identity.name == 'updated_name'

    except CLIError:
        assert False

    # publish identity
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityPublish()
        result = cmd.execute(args)
        assert result is None

    except CLIError:
        assert False

    # discover all identities known to the node
    try:
        args = {
            'address': f"{address[0]}:{address[1]}"
        }

        cmd = IdentityDiscover()
        result = cmd.execute(args)
        assert result is not None
        assert keystore.identity.id in result

        identity: Identity = result[keystore.identity.id]
        assert identity.name == 'updated_name'

    except CLIError:
        assert False



def test_cli_credentials(temp_dir):
    """Test CLI credentials management workflow (list, add, remove)."""
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

    # list all credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
        }

        cmd = CredentialsList()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert len(result['credentials']) == 0

    except CLIError:
        assert False

    # add credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'url': 'url',
            'login': 'login',
            'personal_access_token': 'personal-access-token'
        }

        cmd = CredentialsAddGithubCredentials()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert result['credentials'] is not None

    except CLIError:
        assert False

    # list all credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
        }

        cmd = CredentialsList()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert len(result['credentials']) == 1

    except CLIError:
        assert False

    # remove credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
            'credential': 'github:url',
            'confirm': True
        }

        cmd = CredentialsRemove()
        result = cmd.execute(args)
        assert result is not None
        assert 'removed' in result
        assert len(result['removed']) == 1

    except CLIError:
        assert False

    # list all credentials
    try:
        args = {
            'keystore': temp_dir,
            'keystore-id': keystore.identity.id,
            'password': 'password',
        }

        cmd = CredentialsList()
        result = cmd.execute(args)
        assert result is not None
        assert 'credentials' in result
        assert len(result['credentials']) == 0

    except CLIError:
        assert False


