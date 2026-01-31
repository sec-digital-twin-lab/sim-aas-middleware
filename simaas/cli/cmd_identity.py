import json
import os
import subprocess
from io import StringIO
from typing import Optional

import paramiko
from InquirerPy.base import Choice
from tabulate import tabulate

from simaas.core.errors import CLIError
from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, get_available_keystores, \
    prompt_for_confirmation, prompt_for_password, prompt_if_missing, prompt_for_keystore_selection, \
    prompt_for_selection, load_keystore, extract_address
from simaas.core.schemas import GithubCredentials, SSHCredentials, KeystoreContent
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.helpers import determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy

logger = Logging.get('cli')


class IdentityCreate(CLICommand):
    def __init__(self):
        super().__init__('create', 'creates a new identity', arguments=[
            Argument('--name', dest='name', action='store', help="name of the identity"),
            Argument('--email', dest='email', action='store', help="email of the identity")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'name', prompt_for_string, message="Enter name:")
        prompt_if_missing(args, 'email', prompt_for_string, message="Enter email:")
        prompt_if_missing(args, 'password', prompt_for_password)

        keystore = Keystore.new(args['name'], args['email'], path=args['keystore'], password=args['password'])
        identity = keystore.identity

        print("New keystore created!")
        print(f"- Identity: {identity.name}/{identity.email}/{identity.id}")
        print(f"- Signing Key: {keystore.signing_key.info()}")
        print(f"- Encryption Key: {keystore.encryption_key.info()}")

        return {
            'keystore': keystore
        }


class IdentityRemove(CLICommand):
    def __init__(self):
        super().__init__('remove', 'removes an existing identity', arguments=[
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require user confirmation to delete keystore"),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        load_keystore(args, ensure_publication=False)

        # confirm removal (if applicable)
        if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                             message=f"Remove keystore {args['keystore-id']}?", default=False):

            # delete the keystore
            keystore_path = os.path.join(args['keystore'], f"{args['keystore-id']}.json")
            os.remove(keystore_path)
            print(f"Keystore {args['keystore-id']} deleted.")

        else:
            print("Aborting.")

        return None


class IdentityShow(CLICommand):
    def __init__(self):
        super().__init__('show', 'shows details about a keystore', arguments=[])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'keystore-id', prompt_for_keystore_selection,
                          path=args['keystore'],
                          message="Select the keystore:")

        # read the keystore file
        keystore_path = os.path.join(args['keystore'], f"{args['keystore-id']}.json")
        if not os.path.exists(keystore_path):
            print(f"No keystore found file found at {keystore_path}")
            return {
                'content': None
            }

        # show the public information
        with open(keystore_path, 'r') as f:
            content = KeystoreContent.model_validate(json.load(f))
        print("Keystore details:")
        print(f"- Id: {content.iid}")
        print(f"- Name: {content.profile.name}")
        print(f"- Email: {content.profile.email}")
        print(f"- Nonce: {content.nonce}")
        print("- Assets:")
        for key, content in content.assets.items():
            if content['type'] in ['KeyPairAsset', 'MasterKeyPairAsset']:
                print(f"    - {key}: {content['info']}")
            else:
                print(f"    - {key}")

        return {
            'content': content
        }


class IdentityList(CLICommand):
    def __init__(self):
        super().__init__('list', 'lists all identities found in the keystore directory')

    def execute(self, args: dict) -> Optional[dict]:
        available = get_available_keystores(args['keystore'])
        if len(available) > 0:
            print(f"Found {len(available)} keystores in '{args['keystore']}':")

            # headers
            lines = [
                ['NAME', 'EMAIL', 'KEYSTORE/IDENTITY ID'],
                ['----', '-----', '--------------------']
            ]

            # list
            lines += [
                [item.profile.name, item.profile.email, item.iid] for item in available
            ]

            print(tabulate(lines, tablefmt="plain"))
        else:
            print(f"No keystores found in '{args['keystore']}'.")

        return {
            'available': available
        }


class IdentityPublish(CLICommand):
    def __init__(self):
        super().__init__('publish', 'publishes an identity update to a node', arguments=[
            Argument('--address', dest='address', action='store', required=False,
                     help="the address (host:port) of the node")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        # prompt for the address (if missing)
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        proxy = NodeDBProxy(extract_address(args['address']))
        proxy.update_identity(keystore.identity)
        print(f"Published identity of keystore {args['keystore-id']}")

        return None


class IdentityDiscover(CLICommand):
    def __init__(self):
        super().__init__('discover', 'retrieves a list of all identities known to a node', arguments=[
            Argument('--address', dest='address', action='store', required=False,
                     help="the address (host:port) of the node")
        ])

    def execute(self, args) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter address of node for discovery:",
                          default=determine_default_rest_address())

        proxy = NodeDBProxy(extract_address(args['address']))
        identities = proxy.get_identities()
        if len(identities) == 0:
            print("No identities discovered.")
        else:
            print(f"Discovered {len(identities)} identities:")

            # headers
            lines = [
                ['NAME', 'EMAIL', 'IDENTITY ID'],
                ['----', '-----', '-----------']
            ]

            # list
            lines += [
                [item.name, item.email, item.id] for item in identities.values()
            ]

            print(tabulate(lines, tablefmt="plain"))

        return identities


class IdentityUpdate(CLICommand):
    def __init__(self):
        super().__init__('update', 'updates the profile of the identity', arguments=[
            Argument('--name', dest='name', action='store', help="name of the identity"),
            Argument('--email', dest='email', action='store', help="email of the identity")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        print("Keystore details:")
        print(f"- Name: {keystore.identity.name}")
        print(f"- Email: {keystore.identity.email}")

        name = args['name'] if 'name' in args else None
        email = args['email'] if 'email' in args else None

        # do we have an updated name and/or email?
        if name or email:
            name = name if name else keystore.identity.name
            email = email if email else keystore.identity.email

        # if we have neither, ask interactively
        else:
            name = prompt_for_string("Enter name:", default=keystore.identity.name)
            email = prompt_for_string("Enter email address:", default=keystore.identity.email)

        # update if and as needed
        if keystore.identity.name != name or keystore.identity.email != email:
            print("Updating profile.")
            keystore.update_profile(name=name, email=email)

        else:
            print("Nothing to update.")

        return {
            'keystore': keystore
        }


class CredentialsAddSSHCredentials(CLICommand):
    def __init__(self):
        super().__init__('ssh', 'adds SSH credentials to the keystore', arguments=[
            Argument('--name', dest='name', action='store', help="name used to identify this SSH credential"),
            Argument('--host', dest='host', action='store', help="host used to connect the remote machine"),
            Argument('--login', dest='login', action='store', help="login used for connecting to remote machine"),
            Argument('--key', dest='key', action='store', help="path to the key file"),
            Argument('--passphrase', dest='passphrase', action='store', help="passphrase for the key")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        prompt_if_missing(args, 'name', prompt_for_string, message="Enter name:")
        prompt_if_missing(args, 'host', prompt_for_string, message="Enter host:")
        prompt_if_missing(args, 'login', prompt_for_string, message="Enter login:")
        prompt_if_missing(args, 'key', prompt_for_string, message="Enter path to key file:")
        prompt_if_missing(args, 'passphrase', prompt_for_string, message="Enter key passphrase (leave empty if none):")

        # read key
        if not os.path.isfile(args['key']):
            raise CLIError(f"SSH key not found at {args['key']}")
        else:
            with open(args['key'], 'r') as f:
                key_content = f.read()

        # update the keystore
        passphrase = args['passphrase'] if len(args['passphrase']) > 0 else None
        credentials = SSHCredentials(host=args['host'], login=args['login'], key=key_content, passphrase=passphrase)
        keystore.ssh_credentials.update(args['name'], credentials)
        keystore.sync()
        print("Credential successfully created.")

        return {
            'credentials': credentials
        }


class CredentialsAddGithubCredentials(CLICommand):
    def __init__(self):
        super().__init__('github', 'adds Github credentials to the keystore', arguments=[
            Argument('--url', dest='url', action='store', help="URL of the repository (also used as identifier "
                                                               "for this Github credential)"),
            Argument('--login', dest='login', action='store', help="login used to connect the remote machine"),
            Argument('--personal-access-token', dest='personal_access_token', action='store',
                     help="personal access token for the login"),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        prompt_if_missing(args, 'url', prompt_for_string, message="Enter repository URL:")
        prompt_if_missing(args, 'login', prompt_for_string, message="Enter login:")
        prompt_if_missing(args, 'personal_access_token', prompt_for_string, message="Enter personal access token:")

        # update the keystore
        credentials = GithubCredentials(login=args['login'], personal_access_token=args['personal_access_token'])
        keystore.github_credentials.update(args['url'], credentials)
        keystore.sync()
        print("Credential successfully created.")

        return {
            'credentials': credentials
        }


class CredentialsRemove(CLICommand):
    def __init__(self):
        super().__init__('remove', 'removes credentials from a keystore', arguments=[
            Argument('--credential', dest='type', action='store',
                     help="id of the credential, following the pattern <type>:<name> where type must be "
                          "either 'ssh' or 'github'"),
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require user confirmation to delete credential")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        # collect all the removable credentials
        found = {}
        removable = []
        for name in keystore.ssh_credentials.list():
            label = f"[SSH] {name}"
            item = {
                'asset': 'ssh',
                'label': label,
                'key': name
            }
            found[f'ssh:{name}'] = item
            removable.append(Choice(value=item, name=label))

        for name in keystore.github_credentials.list():
            label = f"[Github] {name}"
            item = {
                'asset': 'github',
                'label': label,
                'key': name
            }
            found[f'github:{name}'] = item
            removable.append(Choice(value=item, name=label))

        removed = []
        if 'credential' in args and args['credential'] is not None:
            if args['credential'] not in found:
                raise CLIError(f"Credential {args['credential']} not found. Aborting.")

            # confirm removal (if applicable)
            item = found[args['credential']]
            if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                                 message=f"Remove credential {args['credential']}?", default=False):
                print(f"Removing {item['label']}...", end='')
                if item['asset'] == 'ssh':
                    keystore.ssh_credentials.remove(item['key'])
                    removed.append(f"ssh:{item['key']}")
                    print("Done")
                elif item['asset'] == 'github':
                    keystore.github_credentials.remove(item['key'])
                    removed.append(f"github:{item['key']}")
                    print("Done")
                keystore.sync()
            else:
                print("Aborting.")

        else:
            # prompt for selection
            if len(removable) == 0:
                raise CLIError("No credentials found. Aborting.")

            # any items selected for removal?
            items = prompt_for_selection(removable, 'Select the credentials to be removed:', allow_multiple=True)
            if len(items) == 0:
                raise CLIError("Nothing to remove. Aborting.")

            # confirm removal (if applicable)
            if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                                 message="Remove the selected credentials?", default=False):
                for item in items:
                    print(f"Removing {item['label']}...", end='')
                    if item['asset'] == 'ssh':
                        keystore.ssh_credentials.remove(item['key'])
                        removed.append(f"ssh:{item['key']}")
                        print("Done")
                    elif item['asset'] == 'github':
                        keystore.github_credentials.remove(item['key'])
                        removed.append(f"github:{item['key']}")
                        print("Done")
                keystore.sync()
            else:
                print("Aborting.")

        return {
            'removed': removed
        }


class CredentialsTestSSHCredentials(CLICommand):
    def __init__(self):
        super().__init__('ssh', 'tests SSH credentials', arguments=[
            Argument('--name', dest='name', action='store', help="name used to identify this SSH credential")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        prompt_if_missing(args, 'name', prompt_for_selection,
            choices=[Choice(name=name, value=name) for name in keystore.ssh_credentials.list()],
            message="Select profile:", allow_multiple=False
        )

        # do we have SSH credentials with this name?
        if args['name'] not in keystore.ssh_credentials.list():
            raise CLIError(f"SSH credentials not found: {args['name']}")

        # get the credentials
        ssh_credentials = keystore.ssh_credentials.get(args['name'])

        # test the ssh connection
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            private_key = paramiko.RSAKey.from_private_key(StringIO(ssh_credentials.key), password=ssh_credentials.passphrase)
            ssh_client.connect(hostname=ssh_credentials.host, username=ssh_credentials.login, pkey=private_key)
            returncode = 0
            print("SSH credentials test successful.")

        except Exception as e:
            returncode = -1
            print(f"SSH credentials test unsuccessful -> {e}")

        return {
            'returncode': returncode
        }


class CredentialsTestGithubCredentials(CLICommand):
    def __init__(self):
        super().__init__('github', 'tests Github credentials', arguments=[
            Argument('--url', dest='url', action='store', help="URL of the repository (also used as identifier "
                                                               "for this Github credential)"),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        prompt_if_missing(args, 'url', prompt_for_selection,
            choices=[Choice(name=name, value=name) for name in keystore.github_credentials.list()],
            message="Select repository URL:", allow_multiple=False
        )

        # do we have Github credentials for this URL?
        if args['url'] not in keystore.github_credentials.list():
            raise CLIError(f"Github credentials not found for {args['url']}")

        # get the credentials
        github_credentials = keystore.github_credentials.get(args['url'])

        url = args['url']
        insert = f"{github_credentials.login}:{github_credentials.personal_access_token}@"
        index = url.find('github.com')
        url = url[:index] + insert + url[index:]

        repo_name = url[url.find('github.com') + 11:]
        print(f"repo_name: {repo_name}")

        result = subprocess.run(['curl', '-H', f"Authorization: token {github_credentials.personal_access_token}",
                                 f"https://api.github.com/repos/{repo_name}"], capture_output=True)
        if result.returncode == 0:
            print("Github credentials test successful.")
        else:
            print("Github credentials test unsuccessful.\n"
                  f"- stdout: {result.stdout.decode('utf-8')}\n"
                  f"- stderr: {result.stdout.decode('utf-8')}")

        return {
            'returncode': result.returncode
        }


class CredentialsList(CLICommand):
    def __init__(self):
        super().__init__('list', 'lists credentials of the keystore', arguments=[])

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=False)

        # headers
        lines = [
            ['TYPE', 'CREDENTIAL NAME', 'DETAILS'],
            ['----', '---------------', '-------']
        ]

        credentials = []
        for name in keystore.ssh_credentials.list():
            c = keystore.ssh_credentials.get(name)
            credentials.append(c)
            lines.append(['SSH', name, f"{c.login}@{c.host}"])

        for name in keystore.github_credentials.list():
            c = keystore.github_credentials.get(name)
            credentials.append(c)
            lines.append(['Github', name, c.login])

        if len(credentials) == 0:
            print("No credentials found.")

        else:
            print(f"Found {len(lines)-2} credentials:")
            print(tabulate(lines, tablefmt="plain"))

        return {
            'credentials': credentials
        }
