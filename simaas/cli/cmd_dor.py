import json
import os
from typing import Optional, Dict, List

from InquirerPy.base import Choice
from tabulate import tabulate

from simaas.core.errors import CLIError, RemoteError
from simaas.cli.helpers import CLICommand, Argument, prompt_if_missing, prompt_for_string, \
    prompt_for_keystore_selection, prompt_for_confirmation, prompt_for_selection, prompt_for_tags, load_keystore, \
    get_nodes_by_service, extract_address, prompt_for_identity_selection, prompt_for_data_objects, \
    deserialise_tag_value, label_data_object, shorten_id, label_identity
from simaas.core.helpers import encrypt_file
from simaas.core.identity import Identity
from simaas.dor.api import DORProxy
from simaas.helpers import determine_default_rest_address
from simaas.dor.schemas import DataObject
from simaas.nodedb.api import NodeDBProxy


def _require_dor(args: dict) -> DORProxy:
    prompt_if_missing(args, 'address', prompt_for_string,
                      message="Enter the node's REST address",
                      default=determine_default_rest_address())

    db = NodeDBProxy(extract_address(args['address']))
    if db.get_node().dor_service.lower() == 'none':
        raise CLIError(f"Node at {args['address'][0]}:{args['address'][1]} does "
                              f"not provide a DOR service. Aborting.")

    return DORProxy(extract_address(args['address']))


class DORAdd(CLICommand):
    def __init__(self) -> None:
        super().__init__('add', 'adds a data object', arguments=[
            Argument('--restrict-access', dest="restrict_access", action='store_const', const=True,
                     help="indicates that access to this data object should be restricted"),
            Argument('--encrypt-content', dest="content_encrypted", action='store_const', const=True,
                     help="indicates that the content of the data object should be encrypted"),
            Argument('--assume-creator', dest="assume_creator", action='store_const', const=True,
                     help="assumes that the user uploading the data object is also the creator"),
            Argument('--data-type', dest='data-type', action='store',
                     help="the data type of the data object"),
            Argument('--data-format', dest='data-format', action='store',
                     help="the data format of the data object"),
            Argument('file', metavar='file', type=str, nargs=1,
                     help="file containing the content of the data object")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # get data type and format
        prompt_if_missing(args, 'data-type', prompt_for_string, message="Enter the data type of the data object:")
        prompt_if_missing(args, 'data-format', prompt_for_string, message="Enter the data format of the data object:")

        # check if the file exists
        if not os.path.isfile(args['file'][0]):
            raise CLIError(f"No file found at '{args['file']}'. Aborting.")

        # set some access/content parameters
        restrict_access = args['restrict_access'] is True
        content_encrypted = args['content_encrypted'] is True
        content_key = None

        # should the data object be encrypted?
        obj_path = args['file'][0]
        if content_encrypted:
            # create key for this data object and encrypt data object in chunks
            obj_path_temp = os.path.join(args['temp-dir'], 'obj.protected')
            content_key = encrypt_file(obj_path, destination_path=obj_path_temp).decode('utf-8')
            obj_path = obj_path_temp

        # determine creators
        creators = [keystore.identity]
        if not args['assume_creator']:
            creators = prompt_for_identity_selection(
                address=extract_address(args['address']),
                message='Select all identities that are co-creators of this data object:',
                allow_multiple=True
            )

        # connect to the DOR and add the data object
        dor = DORProxy(extract_address(args['address']))
        meta = dor.add_data_object(obj_path, keystore.identity, restrict_access, content_encrypted,
                                   args['data-type'], args['data-format'], creators)
        obj_id = meta.obj_id

        # do some simple tagging
        meta = dor.update_tags(obj_id, keystore, [
            DataObject.Tag(key='name', value=os.path.basename(args['file'][0]))
        ])

        # if we used encryption, store the content key
        if content_encrypted:
            keystore.content_keys.update(obj_id, content_key)
            print(f"Content key for object {obj_id} added to keystore.")

            os.remove(obj_path)

        print(f"Data object added: {json.dumps(meta.model_dump(), indent=4)}")

        return {
            'obj': meta
        }


class DORMeta(CLICommand):
    def __init__(self):
        super().__init__('meta', 'retrieves the meta information of a data object', arguments=[
            Argument('--obj-id', dest='obj-id', action='store', help="the id of the data object")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        dor = _require_dor(args)

        # do we have an object id?
        if not args['obj-id']:
            # determine object ids for downloading
            args['obj-id'] = prompt_for_data_objects(extract_address(args['address']),
                                                     message="Select data object:",
                                                     allow_multiple=False)

        # get the meta information
        meta = dor.get_meta(args['obj-id'])
        if not meta:
            raise CLIError(f"No data object with id={args['obj-id']}")

        print(json.dumps(meta.model_dump(), indent=4))

        return {
            'obj': meta
        }


class DORDownload(CLICommand):
    def __init__(self):
        super().__init__('download', 'retrieves the contents of a data object', arguments=[
            Argument('--destination', dest='destination', action='store',
                     help="directory where to store the data object content(s)"),
            Argument('obj-ids', metavar='obj-ids', type=str, nargs='*',
                     help="the ids of the data object that are to be downloaded"),

        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'destination', prompt_for_string,
                          message="Enter the destination folder",
                          default=os.environ.get('HOME', os.path.expanduser('~')))

        dor = _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have an object id?
        if not args['obj-ids']:
            # determine object ids for downloading
            args['obj-ids'] = prompt_for_data_objects(extract_address(args['address']),
                                                      message="Select data object to be downloaded:",
                                                      filter_by_owner=keystore.identity, allow_multiple=True)

            # get the meta information for the objects
            downloadable = [dor.get_meta(obj_id) for obj_id in args['obj-ids']]

        else:
            # check if the object ids exist/owned by this entity or if the entity has access
            result: list[DataObject] = dor.search(owner_iid=keystore.identity.id)
            result: dict[str, DataObject] = {obj.obj_id: obj for obj in result}
            downloadable = []
            for obj_id in args['obj-ids']:
                if obj_id not in result:
                    print(f"Ignoring data object '{shorten_id(obj_id)}': does not exist or is "
                          f"not owned by '{label_identity(keystore.identity)}'")

                elif result[obj_id].access_restricted and keystore.identity.id not in result[obj_id].access:
                    print(f"Ignoring data object '{shorten_id(obj_id)}': '{label_identity(keystore.identity)}' "
                          f"does not have access.")

                else:
                    downloadable.append(result[obj_id])

        # do we have removable data objects?
        if len(downloadable) == 0:
            raise CLIError("No data objects available for download. Aborting.")

        # download the data objects
        dor = DORProxy(extract_address(args['address']))
        result: Dict[str, str] = {}
        for obj in downloadable:
            download_path = os.path.join(args['destination'], f"{obj.obj_id}.{obj.data_format}")
            print(f"Downloading {shorten_id(obj.obj_id)} to {download_path} ...", end='')
            try:
                dor.get_content(obj.obj_id, keystore, download_path)
                result[obj.obj_id] = download_path
                print("Done")

            except RemoteError as e:
                print(f"{e.reason} details: {e.details}")

        return result


class DORRemove(CLICommand):
    def __init__(self) -> None:
        super().__init__('remove', 'removes a data object', arguments=[
            Argument('--confirm', dest="confirm", action='store_const', const=True,
                     help="do not require user confirmation to delete data object"),
            Argument('obj-ids', metavar='obj-ids', type=str, nargs='*',
                     help="the ids of the data object that are to be deleted")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have object ids?
        if len(args['obj-ids']) == 0:
            args['obj-ids'] = prompt_for_data_objects(extract_address(args['address']),
                                                      message="Select data objects to be removed:",
                                                      filter_by_owner=keystore.identity, allow_multiple=True)

        else:
            # check if the object ids exist/owned by this entity
            dor = DORProxy(extract_address(args['address']))
            result = dor.search(owner_iid=keystore.identity.id)
            result = {obj.obj_id: obj for obj in result}
            removable = []
            for obj_id in args['obj-ids']:
                if obj_id not in result:
                    print(f"Ignoring data object '{obj_id}': does not exist or is not owned by "
                          f"'{keystore.identity.name}/{keystore.identity.email}/{keystore.identity.id}'")
                else:
                    removable.append(obj_id)
            args['obj-ids'] = removable

        # do we have removable data objects?
        if len(args['obj-ids']) == 0:
            raise CLIError("No removable data objects. Aborting.")

        # remove data objects
        dor = DORProxy(extract_address(args['address']))
        removed = []
        for obj_id in args['obj-ids']:
            if prompt_if_missing(args, 'confirm', prompt_for_confirmation,
                                 message=f"Delete data object {obj_id}?", default=False):
                dor.delete_data_object(obj_id, keystore)
                removed.append(obj_id)
                print(f"Deleted {obj_id}.")

        return {
            'removed': removed
        }


class DORSearch(CLICommand):
    def __init__(self) -> None:
        super().__init__('search', 'searches for data objects', arguments=[
            Argument('--own', dest="own", action='store_const', const=True,
                     help="limits the search to data objects owned by the identity used (refer to --keystore-id)"),

            Argument('--data-type', dest='data-type', action='store',
                     help="only search for data objects with this data type"),

            Argument('--data-format', dest='data-format', action='store',
                     help="only search for data objects with this data format"),

            Argument('pattern', metavar='pattern', type=str, nargs="*",
                     help="limits the search to data objects whose tag (key or value) contains the pattern(s)")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        # determine the owner iid to limit the search (if applicable)
        owner_iid = None
        if args['own'] is not None:
            prompt_if_missing(args, 'keystore-id', prompt_for_keystore_selection, path=args['keystore'],
                              message="Select the owner:")
            owner_iid = args['keystore-id']

        # get a list of nodes in the network
        result = {}
        dor_nodes, _ = get_nodes_by_service(extract_address(args['address']))
        for node in dor_nodes:
            # create proxies
            node_dor = DORProxy(node.rest_address)
            node_db = NodeDBProxy(node.rest_address)

            # perform the search
            search_result = node_dor.search(patterns=args.get('pattern'), data_type=args.get('data-type'),
                                            data_format=args.get('data-format'), owner_iid=owner_iid)

            # print search results
            if search_result:
                print(f"Found {len(search_result)} data objects at {node.identity.id}/"
                      f"{node.rest_address[0]}:{node.rest_address[1]} that match the criteria:")

                # headers
                lines = [
                    ['OBJECT ID', 'OWNER', 'DATA TYPE', 'DATA FORMAT', 'TAGS'],
                    ['---------', '-----', '---------', '-----------', '----']
                ]

                for item in search_result:
                    owner: Identity = node_db.get_identity(item.owner_iid)
                    tags = [
                        f"{key}: {value if isinstance(value, (str, bool, int, float)) else '<...>'}" if value else key
                        for key, value in item.tags.items()
                    ]

                    lines.append([
                        shorten_id(item.obj_id), label_identity(owner, truncate=True),
                        item.data_type, item.data_format, '\n'.join(tags)
                    ])

                    result[item.obj_id] = item

                print(tabulate(lines, tablefmt="plain"))
                print()

            else:
                print(
                    f"No data objects found at {shorten_id(node.identity.id)}/"
                    f"{node.rest_address[0]}:{node.rest_address[1]} that match the criteria.")

        return result


class DORTag(CLICommand):
    def __init__(self) -> None:
        super().__init__('tag', 'add/update tags of a data object', arguments=[
            Argument('--obj-id', dest='obj-id', action='store',
                     help="the id of the data object"),

            Argument('tags', metavar='tags', type=str, nargs='*',
                     help="the tags (given as \'key=value\' pairs) to be used for the data object")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        dor = _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have an object id?
        if args['obj-id'] is None:
            args['obj-id'] = prompt_for_data_objects(extract_address(args['address']),
                                                     message="Select data objects for tagging:",
                                                     filter_by_owner=keystore.identity,
                                                     allow_multiple=True)
        else:
            args['obj-id'] = [args['obj-id']]

        # check if the object ids exist/owned by this entity
        result: List[DataObject] = dor.search(owner_iid=keystore.identity.id)
        result: List[str] = [item.obj_id for item in result]
        found = []
        for obj_id in args['obj-id']:
            if obj_id not in result:
                print(f"Data object '{shorten_id(obj_id)}' does not exist or is not owned by "
                      f"'{label_identity(keystore.identity)}'. Skipping.")
            else:
                found.append(obj_id)

        # do we have any data objects?
        if len(found) == 0:
            raise CLIError("No data objects found. Aborting.")

        # do we have valid tags?
        if args['tags']:
            # check if the tags are valid
            valid_tags = []
            for tag in args['tags']:
                if tag.count('=') > 1:
                    print(f"Invalid tag '{tag}'. Ignoring.")
                elif tag.count('=') == 0:
                    valid_tags.append(DataObject.Tag(key=tag))
                else:
                    tag = tag.split("=")
                    valid_tags.append(deserialise_tag_value(DataObject.Tag(key=tag[0], value=tag[1])))

        else:
            valid_tags = prompt_for_tags("Enter a tag (key=value) or press return if done:")

        # do we have valid tags?
        if len(valid_tags) == 0:
            raise CLIError("No valid tags found. Aborting.")

        # update the tags
        result: Dict[str, DataObject] = {}
        for obj_id in found:
            print(f"Updating tags for data object {obj_id}...", end='')
            result[obj_id] = dor.update_tags(obj_id, keystore, valid_tags)
            print("Done")

        return result


class DORUntag(CLICommand):
    def __init__(self) -> None:
        super().__init__('untag', 'removes tags from a data object', arguments=[
            Argument('--obj-id', dest='obj-id', action='store',
                     help="the id of the data object"),

            Argument('keys', metavar='keys', type=str, nargs='*',
                     help="the tags (identified by their key) to be removed from the data object")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        dor = _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have an object id?
        if args['obj-id'] is None:
            args['obj-id'] = prompt_for_data_objects(extract_address(args['address']),
                                                     message="Select data object for untagging:",
                                                     filter_by_owner=keystore.identity,
                                                     allow_multiple=False)

        else:
            # check if the object ids exist/owned by this entity
            result = dor.search(owner_iid=keystore.identity.id)
            result = {item.obj_id: item for item in result}
            if args['obj-id'] not in result:
                raise CLIError(f"Data object '{args['obj-id']}' does not exist or is not owned by "
                                      f"'{label_identity(keystore.identity)}'. Aborting.")

        # do we have tags?
        meta = dor.get_meta(args['obj-id'])
        if not args['keys']:
            choices = []
            for key, value in meta. tags.items():
                if value:
                    choices.append(
                        Choice(key, f"{key}: {value if isinstance(value, (str, bool, int, float)) else '...'}")
                    )
                else:
                    choices.append(Choice(key, key))

            args['keys'] = prompt_for_selection(choices, "Select tags to be removed:", allow_multiple=True)

        # check if the tags are valid
        valid_keys = []
        for key in args['keys']:
            if key not in meta.tags:
                print(f"Invalid key '{key}'. Ignoring.")
            else:
                valid_keys.append(key)

        # do we have valid tags?
        if len(valid_keys) == 0:
            raise CLIError("No valid keys found. Aborting.")

        # update the tags
        print(f"Removing tags for data object {shorten_id(args['obj-id'])}...", end='')
        obj = dor.remove_tags(args['obj-id'], keystore, valid_keys)
        print("Done")

        return {
            'obj': obj
        }


class DORAccessShow(CLICommand):
    def __init__(self) -> None:
        super().__init__('show', 'shows the identities who have been granted access to a data object', arguments=[
            Argument('--obj-id', dest='obj-id', action='store', required=False,
                     help="the id of the data object"),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        dor = _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # get all data objects by this user
        result = dor.search(owner_iid=keystore.identity.id)
        if not result:
            raise CLIError("No data objects found. Aborting.")

        # do we have object id?
        if not args['obj-id']:
            choices = [Choice(item.obj_id, label_data_object(item)) for item in result]
            args['obj-id'] = prompt_for_selection(choices, "Select data object:", allow_multiple=False)

        # check if the object id exists
        result = {item.obj_id: item for item in result}
        if args['obj-id'] not in result:
            raise CLIError(f"Data object '{args['obj-id']}' does not exist or is not owned by '"
                                  f"{keystore.identity.name}/"
                                  f"{keystore.identity.email}/"
                                  f"{keystore.identity.id}"
                                  f"'. Aborting.")

        # get the meta information
        meta = result[args['obj-id']]

        if not meta.access_restricted:
            print("Data object is not access restricted: everyone has access.")

        else:
            print("The following identities have been granted access:")
            db = NodeDBProxy(extract_address(args['address']))
            identities = [db.get_identity(iid) for iid in meta.access]
            # headers
            lines = [
                ['NAME', 'EMAIL', 'IDENTITY ID'],
                ['----', '-----', '-----------']
            ]

            # list
            lines += [
                [item.name, item.email, item.id] for item in identities
            ]

            print(tabulate(lines, tablefmt="plain"))

        return {
            'access': meta.access
        }


class DORAccessGrant(CLICommand):
    def __init__(self) -> None:
        super().__init__('grant', 'grants access to one or more data objects', arguments=[
            Argument('--iid', dest='iid', action='store',
                     help="the id of the identity who will be granted access"),

            Argument('obj-ids', metavar='obj-ids', type=str, nargs='*',
                     help="the ids of the data objects to which access will be granted")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        dor = _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have object ids?
        if len(args['obj-ids']) == 0:
            args['obj-ids'] = prompt_for_data_objects(extract_address(args['address']),
                                                      message="Select data objects:",
                                                      filter_by_owner=keystore.identity,
                                                      allow_multiple=True)

        else:
            # check if the object ids exist/owned by this entity
            removable = []
            for obj_id in args['obj-ids']:
                meta = dor.get_meta(obj_id)
                if not meta or meta.owner_iid != keystore.identity.id:
                    raise CLIError(f"Ignoring data object '{obj_id}': does not exist or is not owned by '"
                                          f"{label_identity(keystore.identity)}'")
                else:
                    removable.append(obj_id)
            args['obj-ids'] = removable

        # do we have data objects?
        if len(args['obj-ids']) == 0:
            raise CLIError("No data objects. Aborting.")

        # get the identities known to the node
        db = NodeDBProxy(extract_address(args['address']))
        identities = db.get_identities()

        # do we have an identity?
        if not args['iid']:
            args['iid'] = prompt_for_selection([
                Choice(iid, label_identity(identity)) for iid, identity in identities.items()
            ], message="Select the identity who should be granted access:", allow_multiple=False)

        # is the identity known to the node?
        if args['iid'] not in identities:
            raise CLIError(f"Target node does not know identity {shorten_id(args['iid'])}. Aborting.")

        # grant access
        granted = []
        for obj_id in args['obj-ids']:
            print(f"Granting access to data object {shorten_id(obj_id)} "
                  f"for identity {shorten_id(args['iid'])}...", end='')
            meta = dor.grant_access(obj_id, keystore, identities[args['iid']])
            if args['iid'] not in meta.access:
                print("Failed")
            else:
                granted.append(obj_id)
                print("Done")

        return {
            'granted': granted
        }


class DORAccessRevoke(CLICommand):
    def __init__(self) -> None:
        super().__init__('revoke', 'revokes access to a data object', arguments=[
            Argument('--obj-id', dest='obj-id', action='store',
                     help="the id of the data objects to which access will be revoked"),

            Argument('iids', metavar='iids', type=str, nargs='*',
                     help="the ids of the identities whose access will be revoked")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        dor = _require_dor(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have object ids?
        if args['obj-id'] is None:
            args['obj-id'] = prompt_for_data_objects(extract_address(args['address']),
                                                     message="Select data object:",
                                                     filter_by_owner=keystore.identity,
                                                     allow_multiple=False)

            if args['obj-id'] is None:
                raise CLIError("No data objects found. Aborting.")

        else:
            # check if the object id exists/owned by this entity
            meta = dor.get_meta(args['obj-id'])
            if not meta or meta.owner_iid != keystore.identity.id:
                raise CLIError(f"Ignoring data object '{args['obj-id']}': does not exist or is not owned by '"
                                      f"{label_identity(keystore.identity)}'")

        # collect the identity information of all those that have access
        db = NodeDBProxy(extract_address(args['address']))
        removable = dor.get_meta(args['obj-id']).access
        identities = {}
        choices = []
        for iid in removable:
            identity = db.get_identity(iid)
            if identity:
                identities[identity.id] = identity
                choices.append(Choice(identity.id, label_identity(identity)))

        # do we have removable identities?
        if not args.get('iids'):
            # do we have any choices?
            if not choices:
                raise CLIError("No identities whose access could be revoked.")

            # select the identities to be removed
            args['iids'] = prompt_for_selection(
                choices, message="Select the identities whose access should be removed:", allow_multiple=True)

        # revoke access
        revoked = []
        for iid in args['iids']:
            print(f"Revoking access to data object {shorten_id(args['obj-id'])} "
                  f"for identity {shorten_id(iid)}...", end='')
            meta = dor.revoke_access(args['obj-id'], keystore, identities[iid])
            if iid in meta.access:
                print("Failed")
            else:
                revoked.append(iid)
                print("Done")

        return {
            'revoked': revoked
        }
