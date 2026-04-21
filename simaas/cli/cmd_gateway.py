from typing import Optional, Tuple, List, Dict
from uuid import UUID

from InquirerPy.base import Choice
from tabulate import tabulate

from simaas.cli.cmd_service_gateway import initialise_gateway, proxies
from simaas.cli.helpers import (
    CLICommand, Argument, prompt_if_missing, prompt_for_string,
    prompt_for_password, prompt_for_selection, prompt_for_confirmation,
)
from simaas.core.errors import CLIError
from simaas.core.helpers import hash_string_object
from simaas.gateway.db import DatabaseWrapper, User, APIKey


def _account_label(user: User) -> str:
    status = '' if user.enabled else ' ACCOUNT DISABLED'
    return f"{user.uuid.hex}: {user.name} <{user.email}>{status}"


def _apikey_label(key: APIKey) -> str:
    return f"{key.id} by {key.uuid.hex} ({key.description}): {key.key}"


def _select_users(uuids: List[UUID], action: str) -> Tuple[Dict[UUID, User], List[UUID]]:
    if len(uuids) == 0:
        found, _ = DatabaseWrapper.get_users()
        uuids = prompt_for_selection([
            Choice(value=uuid, name=_account_label(user)) for uuid, user in found.items()
        ], message="Select one or more user accounts:", allow_multiple=True)

    found, missing = DatabaseWrapper.get_users(uuids)
    if len(missing) > 0:
        print("The following user accounts were not found:")
        for uuid in missing:
            print(f"  {uuid.hex}")
        print("")

    if len(found) == 0:
        print(f"No user accounts found that can be {action}. Aborting.")
    else:
        print(f"The following user account(s) will be {action}:")
        for user in found.values():
            print(f"  {_account_label(user)}")
        print()

    return found, missing


def _select_apikeys(key_ids: List[int], action: str) -> Tuple[Dict[int, APIKey], List[int]]:
    if key_ids is None or len(key_ids) == 0:
        found, _ = DatabaseWrapper.get_keys_by_id()
        key_ids = prompt_for_selection([
            Choice(value=key_id, name=_apikey_label(key)) for key_id, key in found.items()
        ], message="Select one or more API keys:", allow_multiple=True)

    found, missing = DatabaseWrapper.get_keys_by_id(key_ids)
    if len(missing) > 0:
        print("The following API keys were not found:")
        for key_id in missing:
            print(f"  {key_id}")
        print("")

    if len(found) == 0:
        print(f"No API keys found that can be {action}. Aborting.")
    else:
        print(f"The following API key(s) will be {action}:")
        for key in found.values():
            print(f"  {_apikey_label(key)}")
        print()

    return found, missing


# --- User Commands ---

class GatewayUserList(CLICommand):
    def __init__(self):
        super().__init__('list', 'shows an overview of all gateway user accounts', arguments=[])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)

        found, _ = DatabaseWrapper.get_users()
        if len(found) == 0:
            print("No user accounts found.")
        else:
            lines = [
                ['UUID', 'NAME', 'EMAIL', 'ENABLED'],
                ['----', '----', '-----', '-------']
            ]
            for uuid, user in found.items():
                lines.append([uuid.hex, user.name, user.email, 'True' if user.enabled else 'False'])

            print(f"Found {len(found)} user accounts:")
            print(tabulate(lines, tablefmt="plain"))

        return {'users': found}


class GatewayUserCreate(CLICommand):
    def __init__(self):
        super().__init__('create', 'creates a new gateway user account', arguments=[
            Argument('--name', dest='name', action='store', help="name of the user"),
            Argument('--email', dest='email', action='store', help="email of the user"),
            Argument('--password', dest='password', action='store', help="password for the user"),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)

        prompt_if_missing(args, 'name', prompt_for_string, message="Enter name:")
        prompt_if_missing(args, 'email', prompt_for_string, message="Enter email:")
        prompt_if_missing(args, 'password', prompt_for_password)

        hashed_password = hash_string_object(args['password']).hex()
        user: User = DatabaseWrapper.create_user(args['name'], args['email'], hashed_password)

        # publish identity to simaas node
        proxies.nodedb.update_identity(user.identity)

        print("New user created!")
        print(f"- UUID: {user.uuid}")
        print(f"- Identity: {user.identity.name}/{user.identity.email}/{user.identity.id}")

        return {'user': user}


class GatewayUserDelete(CLICommand):
    def __init__(self):
        super().__init__('delete', 'delete gateway user account(s)', arguments=[
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require confirmation to delete user accounts"),
            Argument('uuid', metavar='uuid', type=str, nargs='*', help="UUIDs of user accounts to be deleted")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)
        uuids = [UUID(uuid) for uuid in args.get('uuid', [])]

        found, missing = _select_users(uuids, 'deleted')
        if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                             message="Proceed to delete user account(s)?", default=False):
            DatabaseWrapper.delete_users(list(found.keys()))
            print("User account(s) deleted.")
        else:
            print("Aborting.")

        found, missing = DatabaseWrapper.get_users(uuids)
        return {'found': found, 'missing': missing}


class GatewayUserDisable(CLICommand):
    def __init__(self):
        super().__init__('disable', 'disable gateway user account(s)', arguments=[
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require confirmation to disable user accounts"),
            Argument('uuid', metavar='uuid', type=str, nargs='*', help="UUIDs of user accounts to be disabled")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)
        uuids = [UUID(uuid) for uuid in args.get('uuid', [])]

        found, missing = _select_users(uuids, 'disabled')
        if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                             message="Proceed to disable user account(s)?", default=False):
            DatabaseWrapper.update_user_status(list(found.keys()), False)
            print("User account(s) disabled.")
        else:
            print("Aborting.")

        found, missing = DatabaseWrapper.get_users(uuids)
        return {'found': found, 'missing': missing}


class GatewayUserEnable(CLICommand):
    def __init__(self):
        super().__init__('enable', 'enable gateway user account(s)', arguments=[
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require confirmation to enable user accounts"),
            Argument('uuid', metavar='uuid', type=str, nargs='*', help="UUIDs of user accounts to be enabled")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)
        uuids = [UUID(uuid) for uuid in args.get('uuid', [])]

        found, missing = _select_users(uuids, 'enabled')
        if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                             message="Proceed to enable user account(s)?", default=False):
            DatabaseWrapper.update_user_status(list(found.keys()), True)
            print("User account(s) enabled.")
        else:
            print("Aborting.")

        found, missing = DatabaseWrapper.get_users(uuids)
        return {'found': found, 'missing': missing}


class GatewayUserPublish(CLICommand):
    def __init__(self):
        super().__init__('publish', 'publish gateway user identities to the SaaS node', arguments=[])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)
        uuids = [UUID(uuid) for uuid in args.get('uuid', [])]

        result: Tuple[Dict[UUID, User], List[UUID]] = DatabaseWrapper.get_users(uuids, include_keystore=True)
        found: Dict[UUID, User] = result[0]

        print(f"Publishing user identities to {proxies.address}:")
        for uuid, user in found.items():
            print(f"- {user.name} ({user.uuid} / {user.identity.id})")
            proxies.nodedb.update_identity(user.identity)

        print("Done.")


# --- API Key Commands ---

class GatewayKeyList(CLICommand):
    def __init__(self):
        super().__init__('list', 'shows all gateway API keys', arguments=[
            Argument('uuid', metavar='uuid', type=str, nargs='*', help="UUIDs of users of interest")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)
        uuids = [UUID(uuid) for uuid in args['uuid']] if args.get('uuid') else None

        found = DatabaseWrapper.get_keys_by_user(uuids)
        if len(found) == 0:
            print("No API keys found.")
        else:
            lines = [
                ['UUID', 'ID', 'KEY', 'DESCRIPTION'],
                ['----', '--', '---', '-----------']
            ]
            n_total = 0
            for uuid, keys in found.items():
                n_total += len(keys)
                for key in keys:
                    lines.append([uuid.hex, key.id, key.key, key.description])

            print(f"Found {n_total} API keys:")
            print(tabulate(lines, tablefmt="plain"))

        return {'found': found}


class GatewayKeyCreate(CLICommand):
    def __init__(self):
        super().__init__('create', 'creates a new gateway API key', arguments=[
            Argument('--uuid', dest='uuid', action='store', help="UUID of the user"),
            Argument('--description', dest='description', action='store', help="description for the key")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)

        if args.get('uuid') is None:
            found, _ = DatabaseWrapper.get_users()
            if len(found) == 0:
                raise CLIError("No user accounts found.")

            args['uuid'] = prompt_for_selection(message="Select user:", choices=[
                Choice(value=user.uuid, name=_account_label(user)) for user in found.values()
            ])
        else:
            args['uuid'] = UUID(args['uuid'])

        found, missing = DatabaseWrapper.get_users([args['uuid']])
        if len(missing) > 0:
            print(f"No user found with UUID '{args['uuid']}'.")
            return {'key': None}

        user = found[args['uuid']]

        if args.get('description') is None:
            print("No description provided, using '(none)' as default.")
            args['description'] = '(none)'

        key = DatabaseWrapper.generate_key(user, args['description'])

        print("New API key created!")
        print(f"- UUID: {key.uuid}")
        print(f"- Key: {key.key}")
        print(f"- Description: {key.description}")

        return {'key': key}


class GatewayKeyDelete(CLICommand):
    def __init__(self):
        super().__init__('delete', 'delete one or more gateway API keys', arguments=[
            Argument('--key-id', metavar='key_id', type=str, nargs='*', help="IDs of the API keys to be deleted"),
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require confirmation to delete API keys"),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        initialise_gateway(args)
        key_ids = args.get('key_id', [])

        found, missing = _select_apikeys(key_ids, 'deleted')
        if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                             message="Proceed to delete API key(s)?", default=False):
            DatabaseWrapper.delete_keys(list(found.keys()))
            print("API key(s) deleted.")
        else:
            print("Aborting.")

        found, missing = DatabaseWrapper.get_keys_by_id(key_ids)
        return {'found': found, 'missing': missing}
