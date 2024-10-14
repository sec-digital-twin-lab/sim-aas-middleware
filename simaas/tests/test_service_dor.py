import json
import random
import tempfile
import logging
import os

import pytest

from simaas.core.helpers import hash_json_object, symmetric_decrypt, symmetric_encrypt, generate_random_file, \
    hash_file_content
from simaas.dor.exceptions import FetchDataObjectFailedError
from simaas.dor.schemas import DataObject
from simaas.core.helpers import get_timestamp_now, generate_random_string
from simaas.core.logging import Logging
from simaas.rest.exceptions import UnsuccessfulRequestError
from simaas.dor.protocol import DataObjectRepositoryP2PProtocol

Logging.initialise(level=logging.DEBUG)
logger = Logging.get(__name__)


@pytest.fixture()
def unknown_user(extra_keystores):
    return extra_keystores[2]


@pytest.fixture()
def known_users(extra_keystores, node_db_proxy):
    keystores = [extra_keystores[0], extra_keystores[1]]
    for keystore in keystores:
        node_db_proxy.update_identity(keystore.identity)
    return keystores


@pytest.fixture()
def random_content():
    with tempfile.TemporaryDirectory() as tmpdir:
        # create content
        content_path = os.path.join(tmpdir, 'test.json')
        with open(content_path, 'w') as f:
            f.write(json.dumps({
                'a': random.randint(0, 9999)
            }))
        yield content_path


@pytest.fixture()
def receiver(test_context):
    keystore = test_context.create_keystores(1)[0]
    node = test_context.get_node(keystore, enable_rest=True)
    return node


def test_search(dor_proxy):
    result = dor_proxy.search()
    assert (result is not None)


def test_statistics(dor_proxy):
    result = dor_proxy.statistics()
    assert (result is not None)


def test_add_c_multiple_creators(keystore, known_users, dor_proxy, random_content):
    owner = keystore.identity
    c0 = known_users[0]
    c1 = known_users[1]

    result = dor_proxy.add_data_object(random_content, owner, False, False, 'JSON', 'json',
                                       [c0.identity, c1.identity])

    assert(result is not None)
    assert(len(result.created.creators_iid) == 2)
    assert(c0.identity.id in result.created.creators_iid)
    assert(c1.identity.id in result.created.creators_iid)

    result = dor_proxy.add_data_object(random_content, owner, False, False, 'JSON', 'json')
    assert(result is not None)
    assert(len(result.created.creators_iid) == 1)
    assert(owner.id in result.created.creators_iid)


def test_add_c_license(keystore, dor_proxy, random_content):
    owner = keystore.identity

    result = dor_proxy.add_data_object(random_content, owner, False, False, 'JSON', 'json', license_by=True)
    assert(result is not None)
    assert result.license.by
    assert(not result.license.sa)
    assert(not result.license.nc)
    assert(not result.license.nd)


def test_add_c(keystore, dor_proxy, unknown_user, random_content):
    owner = keystore.identity

    # unknown owner
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.add_data_object(random_content, unknown_user.identity, False, False, 'JSON', 'json', [owner])
    assert('Identity not found' in e.value.reason)

    # unknown creator
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.add_data_object(random_content, owner, False, False, 'JSON', 'json', [unknown_user.identity])
    assert ('Identity not found' in e.value.reason)

    result = dor_proxy.add_data_object(random_content, owner, False, False, 'JSON', 'json', [owner])
    assert(result is not None)


def test_add_c_large(test_context, keystore, dor_proxy):
    # this is necessary to avoid getting thousands of 'Calling on_part_data with data' messages...
    mp_logger = logging.getLogger('multipart.multipart')
    mp_logger.setLevel(logging.INFO)

    owner = keystore

    content_path = os.path.join(test_context.testing_dir, 'test.json')

    # TODO: change to use pytest parameterize
    def upload_cycle(size: int) -> (int, float, float, float):
        t0 = get_timestamp_now()
        generate_random_file(content_path, size)
        file_hash = hash_file_content(content_path).hex()
        t1 = get_timestamp_now()
        obj = dor_proxy.add_data_object(content_path, owner.identity, False, False, 'JSON', 'json')
        t2 = get_timestamp_now()
        assert (obj is not None)
        assert (obj.c_hash == file_hash)
        t3 = get_timestamp_now()

        dor_proxy.delete_data_object(obj.obj_id, with_authorisation_by=owner)

        dt0 = (t1 - t0) / 1000.0
        dt1 = (t2 - t1) / 1000.0
        dt2 = (t3 - t2) / 1000.0
        return size, dt0, dt1, dt2

    results = [
        upload_cycle(1 * 1024 * 1024),
        upload_cycle(4 * 1024 * 1024),
        upload_cycle(16 * 1024 * 1024),
        upload_cycle(64 * 1024 * 1024),
        # upload_cycle(256 * 1024 * 1024),
        # upload_cycle(512 * 1024 * 1024),
        # upload_cycle(1024 * 1024 * 1024)
    ]

    print("upload performance: size, generation, upload, hashing")
    for result in results:
        print(f"{result[0]}\t{result[1]}\t{result[2]}\t{result[3]}")


def test_remove(dor_proxy, random_content, known_users):
    c0 = known_users[0]
    c1 = known_users[1]

    result = dor_proxy.add_data_object(random_content, c0.identity, False, False, 'JSON', 'json')
    obj_id = result.obj_id

    # try to delete non-existent object
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.delete_data_object('invalid obj id', with_authorisation_by=c0)
    assert('Data object not found' in e.value.reason)

    # try to delete with wrong authority
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.delete_data_object(obj_id, with_authorisation_by=c1)
    assert('user is not the data object owner' in e.value.details['reason'])

    # try to delete with correct authority
    result = dor_proxy.delete_data_object(obj_id, with_authorisation_by=c0)
    assert(result is not None)
    assert(result.obj_id == obj_id)

    result = dor_proxy.get_meta(obj_id)
    assert(result is None)


def test_get_meta(keystore, dor_proxy, random_content):
    owner = keystore.identity

    result = dor_proxy.add_data_object(random_content, owner, False, False, 'JSON', 'json')
    valid_obj_id = result.obj_id
    invalid_obj_id = 'invalid_obj_id'

    result = dor_proxy.get_meta(invalid_obj_id)
    assert(result is None)

    result = dor_proxy.get_meta(valid_obj_id)
    assert(result is not None)
    assert(result.obj_id == valid_obj_id)


def test_get_content(test_context, keystore, dor_proxy, random_content, unknown_user, known_users):
    owner = keystore.identity

    result = dor_proxy.add_data_object(random_content, owner, True, False, 'JSON', 'json')
    valid_obj_id = result.obj_id
    invalid_obj_id = 'invalid_obj_id'

    download_path = os.path.join(test_context.testing_dir, 'downloaded.json')

    correct_authority = keystore
    unknown_authority = unknown_user
    wrong_authority = known_users[0]

    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.get_content(invalid_obj_id, correct_authority, download_path)
    assert(e.value.details['reason'] == 'data object does not exist')

    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.get_content(valid_obj_id, unknown_authority, download_path)
    assert(e.value.details['reason'] == 'unknown identity')

    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.get_content(valid_obj_id, wrong_authority, download_path)
    assert(e.value.details['reason'] == 'user has no access to the data object content')

    dor_proxy.get_content(valid_obj_id, correct_authority, download_path)
    assert(os.path.isfile(download_path))


def test_get_provenance(test_context, keystore, dor_proxy):
    processor = {
        'repository': 'github.com/source',
        'commit_id': '34534ab',
        'proc_path': '/proc',
        'proc_descriptor': {
            'name': 'proc0',
            'input': [{
                'name': 'a',
                'data_type': 'JSON',
                'data_format': 'json',
                'data_schema': None
            }, {
                'name': 'b',
                'data_type': 'JSON',
                'data_format': 'json',
                'data_schema': None
            }],
            'output': [{
                'name': 'c',
                'data_type': 'JSON',
                'data_format': 'json',
                'data_schema': None
            }]
        }
    }

    owner = keystore.identity

    # create contents
    content_path_a = os.path.join(test_context.testing_dir, 'a.json')
    content_path_c = os.path.join(test_context.testing_dir, 'c.json')
    test_context.create_file_with_content(content_path_a, json.dumps({'v': 1}))
    test_context.create_file_with_content(content_path_c, json.dumps({'v': 3}))
    b_c_hash = hash_json_object({'v': 2}).hex()

    meta_a = dor_proxy.add_data_object(content_path_a, owner, False, False, 'JSON', 'json', recipe=None)
    result = dor_proxy.get_provenance(meta_a.c_hash)
    assert(result is not None)
    assert(meta_a.c_hash in result.data_nodes)
    assert(meta_a.c_hash in result.missing)

    meta_c = dor_proxy.add_data_object(content_path_c, owner, False, False, 'JSON', 'json', recipe={
        'processor': processor,
        'consumes': {
            'a': {
                'c_hash': meta_a.c_hash,
                'data_type': 'JSON',
                'data_format': 'json',
                'content': None
            },
            'b': {
                'c_hash': b_c_hash,
                'data_type': 'JSON',
                'data_format': 'json',
                'content': {'v': 2}
            }
        },
        'product': {
            'c_hash': 'unknown',
            'data_type': 'JSON',
            'data_format': 'json',
            'content': None
        },
        'name': 'c'
    })

    result = dor_proxy.get_provenance(b_c_hash)
    assert(result is not None)

    result = dor_proxy.get_provenance(meta_c.c_hash)
    assert(result is not None)
    assert(len(result.steps) == 1)
    step = result.steps[0]
    assert(step.processor == '93703a2148633f409c2189e56d0e78cf491345b0a4b40873c7b7e6242baea96a')
    assert(step.consumes['a'] == '9ab2253fc38981f5be9c25cf0a34b62cdf334652344bdef16b3d5dbc0b74f2f1')
    assert(step.consumes['b'] == '2b5442799fccc3af2e7e790017697373913b7afcac933d72fb5876de994f659a')
    assert(step.produces['c'] == 'b460644a73d5df6998c57c4eaf43ebc3e595bd06930af6e42d0008f84d91c849')


def test_grant_revoke_access(keystore, dor_proxy, random_content, known_users):
    owner = keystore

    result = dor_proxy.add_data_object(random_content, owner.identity, False, False, 'JSON', 'json')
    obj_id = result.obj_id

    user0 = known_users[0]
    user1 = known_users[1]

    meta = dor_proxy.get_meta(obj_id)
    assert(owner.identity.id == meta.owner_iid)
    assert(owner.identity.id in meta.access)
    assert(user0.identity.id not in meta.access)
    assert(user1.identity.id not in meta.access)

    # try to grant access to a user that doesn't have access yet without being the owner
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.grant_access(obj_id, user0, user1.identity)
    assert(e.value.details['reason'] == 'user is not the data object owner')

    # try to grant access to a user that doesn't have access yet
    meta = dor_proxy.grant_access(obj_id, owner, user1.identity)
    assert (owner.identity.id == meta.owner_iid)
    assert (owner.identity.id in meta.access)
    assert (user0.identity.id not in meta.access)
    assert (user1.identity.id in meta.access)

    # try to grant access to a user that already has access
    meta = dor_proxy.grant_access(obj_id, owner, user1.identity)
    assert (owner.identity.id == meta.owner_iid)
    assert (owner.identity.id in meta.access)
    assert (user0.identity.id not in meta.access)
    assert (user1.identity.id in meta.access)

    # try to revoke access from a user that has access without being the owner
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.revoke_access(obj_id, user0, user1.identity)
    assert(e.value.details['reason'] == 'user is not the data object owner')

    # try to revoke access from a user that has access
    meta = dor_proxy.revoke_access(obj_id, owner, user1.identity)
    assert (owner.identity.id == meta.owner_iid)
    assert (owner.identity.id in meta.access)
    assert (user0.identity.id not in meta.access)
    assert (user1.identity.id not in meta.access)

    # try to revoke access from a user that doesn't have access
    meta = dor_proxy.revoke_access(obj_id, owner, user0.identity)
    assert (owner.identity.id == meta.owner_iid)
    assert (owner.identity.id in meta.access)
    assert (user0.identity.id not in meta.access)
    assert (user1.identity.id not in meta.access)


def test_transfer_ownership(keystore, dor_proxy, random_content, known_users, unknown_user):
    owner = keystore

    meta = dor_proxy.add_data_object(random_content, owner.identity, False, False, 'JSON', 'json')
    obj_id = meta.obj_id

    user0 = known_users[0]
    user1 = known_users[1]
    user2 = unknown_user

    meta = dor_proxy.get_meta(obj_id)
    assert(owner.identity.id == meta.owner_iid)

    # try to transfer ownership without being the owner
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.transfer_ownership(obj_id, user0, user1.identity)
    assert(e.value.details['reason'] == 'user is not the data object owner')

    # try to transfer ownership to an unknown user
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.transfer_ownership(obj_id, owner, user2.identity)
    assert('Identity not found' in e.value.reason)

    # try to transfer ownership to a known user
    meta = dor_proxy.transfer_ownership(obj_id, owner, user0.identity)
    assert (user0.identity.id == meta.owner_iid)


def test_update_remove_tags(keystore, dor_proxy, random_content, known_users):
    owner = keystore

    result = dor_proxy.add_data_object(random_content, owner.identity, False, False, 'JSON', 'json')
    obj_id = result.obj_id

    wrong_user = known_users[0]

    meta = dor_proxy.get_meta(obj_id)
    assert(owner.identity.id == meta.owner_iid)
    assert(len(meta.tags) == 0)

    # try to set tags by non-owner
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.update_tags(obj_id, wrong_user, [DataObject.Tag(key='name', value='abc')])
    assert (e.value.details['reason'] == 'user is not the data object owner')

    # try to set tags by owner
    meta = dor_proxy.update_tags(obj_id, owner, [DataObject.Tag(key='name', value='abc')])
    assert(len(meta.tags) == 1)
    assert('name' in meta.tags)
    assert(meta.tags['name'] == 'abc')

    # try to set tags by owner
    meta = dor_proxy.update_tags(obj_id, owner, [DataObject.Tag(key='name', value='bcd')])
    assert(len(meta.tags) == 1)
    assert('name' in meta.tags)
    assert(meta.tags['name'] == 'bcd')

    # try to remove existing tag by non-owner
    with pytest.raises(UnsuccessfulRequestError) as e:
        dor_proxy.remove_tags(obj_id, wrong_user, ['name'])
    assert (e.value.details['reason'] == 'user is not the data object owner')

    # try to remove non-existing tag by owner
    dor_proxy.remove_tags(obj_id, owner, ['invalid_key'])

    # try to remove existing tag by owner
    meta = dor_proxy.remove_tags(obj_id, owner, ['name'])
    assert (len(meta.tags) == 0)

    # try to set a complex tag by owner
    meta = dor_proxy.update_tags(obj_id, owner, [DataObject.Tag(key='profile', value={
        'name': 'mr a',
        'email': 'somewhere@internet.com'
    })])
    assert(len(meta.tags) == 1)
    assert('profile' in meta.tags)
    assert('name' in meta.tags['profile'])
    assert('email' in meta.tags['profile'])


def test_content_encryption(test_context, known_users, dor_proxy):
    # create content for the data object and encrypt it
    content_plain = "my little secret..."
    content_enc, content_key = symmetric_encrypt(content_plain.encode('utf-8'))
    logger.info(f"content_plain={content_plain}")
    logger.info(f"content_enc={content_enc}")
    logger.info(f"content_key={content_key}")
    content_enc_path = test_context.create_file_with_content('content.enc', content_enc.decode('utf-8'))

    owner1 = known_users[0]
    owner2 = known_users[1]

    # add data object with the encrypted content
    meta = dor_proxy.add_data_object(content_enc_path, owner1.identity, False, True, 'map', 'json')
    obj_id = meta.obj_id

    # transfer ownership now
    protected_content_key2 = owner2.encrypt(content_key).decode('utf-8')
    dor_proxy.transfer_ownership(obj_id, owner1, owner2.identity)

    # we should be able to use the content key to decrypt the content
    unprotected_content_key = owner2.decrypt(protected_content_key2.encode('utf-8'))
    unprotected_content = symmetric_decrypt(content_enc, unprotected_content_key).decode('utf-8')
    assert(unprotected_content == content_plain)

    dor_proxy.delete_data_object(obj_id, owner2)


def test_fetch_data_object(test_context, known_users, dor_proxy, node_db_proxy, node, receiver):
    owner = known_users[0]
    meta = dor_proxy.add_data_object(test_context.generate_zero_file('test000.dat', 1024*1024),
                                     owner.identity, True, False, 'map', 'json')
    obj_id = meta.obj_id

    # update identity
    receiver_identity = receiver.update_identity()
    node_db_proxy.update_identity(receiver_identity)

    protocol = DataObjectRepositoryP2PProtocol(receiver)

    # try to fetch a data object that doesn't exist
    fake_obj_id = 'abcdef'
    meta_path = os.path.join(test_context.testing_dir, f"{fake_obj_id}.meta")
    content_path = os.path.join(test_context.testing_dir, f"{fake_obj_id}.content")
    with pytest.raises(FetchDataObjectFailedError):
        protocol.fetch(node.p2p.address(), fake_obj_id,
                       destination_meta_path=meta_path,
                       destination_content_path=content_path)

    # the receiver does not have permission at this point to receive the data object
    meta_path = os.path.join(test_context.testing_dir, f"{obj_id}.meta")
    content_path = os.path.join(test_context.testing_dir, f"{obj_id}.content")
    with pytest.raises(FetchDataObjectFailedError):
        protocol.fetch(node.p2p.address(), obj_id,
                       destination_meta_path=meta_path,
                       destination_content_path=content_path)

    # grant permission
    meta = dor_proxy.grant_access(obj_id, owner, receiver_identity)
    assert receiver_identity.id in meta.access

    # create user signature to delegate access rights
    token = f"{receiver_identity.id}:{obj_id}"
    signature = receiver.keystore.sign(token.encode('utf-8'))

    # the receiver does have permission at this point to receive the data object
    protocol.fetch(node.p2p.address(), obj_id,
                   destination_meta_path=meta_path,
                   destination_content_path=content_path,
                   user_signature=signature, user_iid=receiver_identity.id)
    assert os.path.isfile(meta_path)
    assert os.path.isfile(content_path)

    dor_proxy.delete_data_object(obj_id, owner)


def test_search_by_content_hashes(test_context, known_users, dor_proxy):
    owner = known_users[0]

    # create data objects
    meta0 = dor_proxy.add_data_object(test_context.generate_random_file(generate_random_string(4), 1024*1024),
                                      owner.identity, False, False, 'map', 'json')
    meta1 = dor_proxy.add_data_object(test_context.generate_random_file(generate_random_string(4), 1024*1024),
                                      owner.identity, False, False, 'map', 'json')
    meta2 = dor_proxy.add_data_object(test_context.generate_random_file(generate_random_string(4), 1024*1024),
                                      owner.identity, False, False, 'map', 'json')
    obj_id0 = meta0.obj_id
    obj_id1 = meta1.obj_id
    obj_id2 = meta2.obj_id
    c_hash0 = meta0.c_hash
    c_hash1 = meta1.c_hash
    c_hash2 = meta2.c_hash

    # search for data objects
    result = dor_proxy.search(c_hashes=[c_hash0, c_hash1])
    logger.info(f"result={result}")
    assert len(result) == 2
    result = {i.obj_id: i.tags for i in result}
    assert obj_id0 in result
    assert obj_id1 in result

    # search for data objects
    result = dor_proxy.search(c_hashes=[c_hash2])
    logger.info(f"result={result}")
    assert len(result) == 1
    result = {i.obj_id: i.tags for i in result}
    assert obj_id2 in result

    dor_proxy.delete_data_object(obj_id0, owner)
    dor_proxy.delete_data_object(obj_id1, owner)
    dor_proxy.delete_data_object(obj_id2, owner)


def test_touch_data_object(test_context, known_users, dor_proxy, random_content):
    owner = known_users[0]

    # create data object
    meta: DataObject = dor_proxy.add_data_object(random_content, owner.identity, False, False, 'JSON', 'json')
    obj_id = meta.obj_id
    last_accessed = meta.last_accessed

    # get the meta information (should not affect last accessed)
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed == last_accessed)

    # get content (should affect last accessed)
    dor_proxy.get_content(obj_id, owner, os.path.join(test_context.testing_dir, 'fetched.json'))
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed > last_accessed)
    last_accessed = meta.last_accessed

    # get the provenance information (should not affect last accessed)
    dor_proxy.get_provenance(meta.c_hash)
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed == last_accessed)

    # grant access (should affect last accessed)
    dor_proxy.revoke_access(obj_id, owner, owner.identity)
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed > last_accessed)
    last_accessed = meta.last_accessed

    # revoke access (should affect last accessed)
    dor_proxy.revoke_access(obj_id, owner, owner.identity)
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed > last_accessed)
    last_accessed = meta.last_accessed

    # transfer ownership (should affect last accessed)
    dor_proxy.transfer_ownership(obj_id, owner, owner.identity)
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed > last_accessed)
    last_accessed = meta.last_accessed

    # update tags (should affect last accessed)
    dor_proxy.update_tags(obj_id, owner, [DataObject.Tag(key='name', value='value')])
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed > last_accessed)
    last_accessed = meta.last_accessed

    # remove tag (should affect last accessed)
    dor_proxy.remove_tags(obj_id, owner, ['name'])
    meta: DataObject = dor_proxy.get_meta(obj_id)
    assert(meta.last_accessed > last_accessed)
