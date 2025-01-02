import getpass
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Optional, Tuple

import docker
from dotenv import load_dotenv
from git import Repo

from simaas.cli.exceptions import CLIRuntimeError
from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_if_missing, load_keystore, \
    default_if_missing
from simaas.core.helpers import get_timestamp_now
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.dor.schemas import ProcessorDescriptor, DataObject, GitProcessorPointer
from simaas.helpers import docker_export_image, determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy

logger = Logging.get('cli')


def clone_repository(repository_url: str, repository_path: str, commit_id: str = None,
                     credentials: Optional[Tuple[str, str]] = None) -> int:
    original_url = repository_url

    # do we have credentials? inject it into the repo URL
    if credentials:
        idx = repository_url.index('github.com')
        url0 = repository_url[:idx]
        url1 = repository_url[idx:]
        repository_url = f"{url0}{credentials[0]}:{credentials[1]}@{url1}"

    # does the destination already exist?
    shutil.rmtree(repository_path, ignore_errors=True)

    try:
        # clone the repo
        Repo.clone_from(repository_url, repository_path)
        repo = Repo(repository_path)

    except Exception as e:
        raise CLIRuntimeError(reason=f"Failed to clone '{original_url}'", details={'exception': str(e)})

    try:
        # checkout a specific commit
        repo.git.checkout(commit_id)

        # determine the commit timestamp
        commit = repo.commit(commit_id)
        commit_timestamp = commit.authored_datetime.timestamp()

        return int(commit_timestamp)

    except Exception as e:
        raise CLIRuntimeError(reason=f"Failed to checkout '{commit_id}'", details={'exception': str(e)})


def build_processor_image(processor_path: str, image_name: str, credentials: Tuple[str, str] = None,
                          force_build: bool = False) -> bool:
    # check if the image already exists
    client = docker.from_env()
    image_existed = False
    try:
        # get a list of all images and check if it has the name.
        for image in client.images.list():
            if image_name in image.tags:
                # if we are forced to build a new image, delete the existing one first
                if force_build:
                    client.images.remove(image.id, force=True)

                image_existed = True
                break

    except Exception as e:
        raise CLIRuntimeError("Deleting existing docker image failed.", details={
            'exception': e
        })

    # build the processor docker image
    if force_build or not image_existed:
        with tempfile.TemporaryDirectory() as tempdir:
            credentials_path = os.path.join(tempdir, "credentials")
            try:
                # assemble the command
                command = ['docker', 'build', '--no-cache']
                if credentials:
                    # write the credentials to file (temporarily)
                    with open(credentials_path, 'w') as f:
                        f.write(f"{credentials[0]}:{credentials[1]}")

                    command.extend(['--secret', f'id=git_credentials,src={credentials_path}'])
                command.extend(['-t', image_name, '.'])

                env = os.environ.copy()
                env['DOCKER_BUILDKIT'] = '1'

                subprocess.run(command, cwd=processor_path, check=True, capture_output=True, text=True, env=env)

            except subprocess.CalledProcessError as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(e.stderr)
                raise CLIRuntimeError("Creating docker image failed", details={
                    'stdout': e.stdout,
                    'stderr': e.stderr,
                    'exception': str(e),
                    'trace': trace
                })

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(trace)
                raise CLIRuntimeError(f"Creating docker image failed: {e}", details={
                    'exception': str(e),
                    'trace': trace
                })

            finally:
                if os.path.isfile(credentials_path):
                    os.remove(credentials_path)

    return image_existed


class ProcBuilderLocal(CLICommand):
    default_force_build = False
    default_keep_image = True

    def __init__(self):
        super().__init__('build-local', 'build a processor from local source', arguments=[
            Argument('--address', dest='address', action='store',
                     help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')"),
            Argument('--force-build', dest="force_build", action='store_const', const=True,
                     help="Force building a processor docker image even if one already exists."),
            Argument('--delete-image', dest="keep_image", action='store_const', const=False,
                     help="Deletes the newly created image after exporting it - note: if an image with the same "
                          "name already existed, this flag will be ignored, effectively resulting in the existing "
                          "image being replaced with the newly created one."),
            Argument('location', metavar='location', type=str, nargs=1,
                     help="the location of the processor")

        ])

    def execute(self, args: dict) -> Optional[dict]:
        load_dotenv()

        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        # load keystore
        keystore = load_keystore(args, ensure_publication=True)

        # determine node has DOR capabilities
        args['address'] = args['address'].split(':')
        node_db = NodeDBProxy(args['address'])
        node_info = node_db.get_node()
        if not node_info.dor_service:
            raise CLIRuntimeError(f"Node at {args['address']} does not support DOR capabilities.")

        default_if_missing(args, 'force_build', self.default_force_build)
        default_if_missing(args, 'keep_image', self.default_keep_image)

        # determine location
        location = args.get('location', [os.getcwd()])
        location = location[0] if location else os.getcwd()
        location = location if location.startswith('/') else os.path.join(os.getcwd(), location)
        location = os.path.abspath(location)
        args['location'] = location
        if not os.path.isdir(args['location']):
            raise CLIRuntimeError(f"Processor path not found: {args['location']}")
        else:
            print(f"Using processor path '{args['location']}'.")

        # see if required files do exist
        missing = []
        for required in ['descriptor.json', 'processor.py', 'Dockerfile']:
            path = os.path.join(args['location'], required)
            if not os.path.isfile(path):
                missing.append(required)

        # anything missing?
        if missing:
            raise CLIRuntimeError(f"Processor folder missing files: {missing}")

        # determine credentials (if any)
        if args.get('git_username') and args.get('git_token'):
            credentials = (args.get('git_username'), args.get('git_token'))
            print(f"Using GitHub credentials from args for user '{credentials[0]}'.")
        elif {'GITHUB_USERNAME', 'GITHUB_TOKEN'}.issubset(os.environ):
            credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])
            print(f"Using GitHub credentials from env for user '{credentials[0]}'.")
        else:
            credentials = None
            print("Not using any GitHub credentials.")

        # read the descriptor
        descriptor_path = os.path.join(args['location'], 'descriptor.json')
        with open(descriptor_path, 'r') as f:
            try:
                descriptor = ProcessorDescriptor.parse_obj(json.load(f))
            except Exception as e:
                raise CLIRuntimeError(f"Cannot read processor descriptor at {descriptor_path}: {e}")

        # calculate hash of processor content
        sha256 = hashlib.sha256()
        for root, _, files in sorted(os.walk(args['location'])):
            for file_name in sorted(files):
                file_path = os.path.join(root, file_name)

                # Update hash with file path to account for structure
                sha256.update(file_path.encode())

                # Read file content and update the hash
                with open(file_path, 'rb') as file:
                    while chunk := file.read(8192):  # Read in chunks for large files
                        sha256.update(chunk)
        content_hash = sha256.hexdigest()
        content_timestamp = get_timestamp_now()
        print(f"Processor content hash: {content_hash}")

        # determine the image name
        processor_path = Path(args['location'])
        repo_name = f"local://{processor_path.parent}"
        user_name = getpass.getuser()
        proc_path = os.path.basename(args['location'])
        image_name = f"{user_name}/{descriptor.name}:{content_hash}"
        print(f"Building image '{image_name}'. This may take a while...")

        # build the image
        image_existed = build_processor_image(args['location'], image_name, credentials=credentials,
                                              force_build=args['force_build'])
        if args['force_build'] or not image_existed:
            print(f"Done building image '{image_name}'.")
        else:
            print(f"Using existing building image '{image_name}'.")

        with tempfile.TemporaryDirectory() as tempdir:
            # export the image
            export_path = os.path.join(tempdir, 'image.tar')
            docker_export_image(image_name, export_path, keep_image=image_existed or args['keep_image'])
            print(f"Done exporting image to '{export_path}'.")

            # upload the image to the DOR and set GPP tags
            dor = DORProxy(args['address'])
            pdi = dor.add_data_object(export_path, keystore.identity, False, False, 'ProcessorDockerImage', 'tar',
                                      tags=[
                                          DataObject.Tag(key='repository', value=repo_name),
                                          DataObject.Tag(key='commit_id', value=content_hash),
                                          DataObject.Tag(key='commit_timestamp', value=content_timestamp),
                                          DataObject.Tag(key='proc_path', value=proc_path),
                                          DataObject.Tag(key='proc_descriptor', value=descriptor.dict()),
                                          DataObject.Tag(key='image_name', value=image_name)
                                      ])
            print(f"Done uploading image to DOR -> object id: {pdi.obj_id}")
            os.remove(export_path)
            return {
                'pdi': pdi
            }


class ProcBuilderGithub(CLICommand):
    default_store_image = False
    default_force_build = False
    default_keep_image = True

    def __init__(self):
        super().__init__('build-github', 'build a processor from Github source', arguments=[
            Argument('--address', dest='address', action='store',
                     help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')"),
            Argument('--repository', dest='repository', action='store', help="URL of the repository"),
            Argument('--commit-id', dest='commit_id', action='store', help="the commit id"),
            Argument('--proc-path', dest='proc_path', action='store', help="path to the processor"),
            Argument('--git-username', dest='git_username', action='store', help="GitHub username"),
            Argument('--git-token', dest='git_token', action='store', help="GitHub personal access token"),
            Argument('--store-image', dest="store_image", action='store_const', const=True,
                     help="Store the image in the DOR not just a reference."),
            Argument('--force-build', dest="force_build", action='store_const', const=True,
                     help="Force building a processor docker image even if one already exists."),
            Argument('--delete-image', dest="keep_image", action='store_const', const=False,
                     help="Deletes the newly created image after exporting it - note: if an image with the same "
                          "name already existed, this flag will be ignored, effectively resulting in the existing "
                          "image being replaced with the newly created one.")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        load_dotenv()

        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        # load keystore
        keystore = load_keystore(args, ensure_publication=True)

        # determine node has DOR capabilities
        args['address'] = args['address'].split(':')
        node_db = NodeDBProxy(args['address'])
        node_info = node_db.get_node()
        if not node_info.dor_service:
            raise CLIRuntimeError("Node at {args['address']} does not support DOR capabilities.")

        prompt_if_missing(args, 'repository', prompt_for_string, message="Enter URL of the repository:")
        prompt_if_missing(args, 'commit_id', prompt_for_string, message="Enter the commit id:")
        prompt_if_missing(args, 'proc_path', prompt_for_string, message="Enter path to the processor:")
        default_if_missing(args, 'store_image', self.default_store_image)
        default_if_missing(args, 'force_build', self.default_force_build)
        default_if_missing(args, 'keep_image', self.default_keep_image)

        print(f"Using repository at {args['repository']} with commit id {args['commit_id']}.")
        print(f"Using processor path '{args['proc_path']}'.")

        # determine credentials (if any)
        if args.get('git_username') and args.get('git_token'):
            credentials = (args.get('git_username'), args.get('git_token'))
            print(f"Using GitHub credentials from args for user '{credentials[0]}'.")
        elif {'GITHUB_USERNAME', 'GITHUB_TOKEN'}.issubset(os.environ):
            credentials = (os.environ['GITHUB_USERNAME'], os.environ['GITHUB_TOKEN'])
            print(f"Using GitHub credentials from env for user '{credentials[0]}'.")
        else:
            credentials = None
            print("Not using any GitHub credentials.")

        with tempfile.TemporaryDirectory() as tempdir:
            # clone the repository and checkout the specified commit
            repo_path = os.path.join(tempdir, 'repository')
            commit_timestamp = clone_repository(args['repository'], repo_path, commit_id=args['commit_id'],
                                                credentials=credentials)
            print(f"Done cloning {args['repository']}.")

            # does the path exist?
            processor_path = os.path.join(repo_path, args['proc_path'])
            if not os.path.isdir(processor_path):
                raise CLIRuntimeError(f"Processor path not found: {processor_path}")

            # see if required files do exist
            missing = []
            for required in ['descriptor.json', 'processor.py', 'Dockerfile']:
                path = os.path.join(processor_path, required)
                if not os.path.isfile(path):
                    missing.append(required)

            # anything missing?
            if missing:
                raise CLIRuntimeError(f"Processor folder missing files: {missing}")

            # read the descriptor
            descriptor_path = os.path.join(processor_path, 'descriptor.json')
            with open(descriptor_path, 'r') as f:
                try:
                    descriptor = ProcessorDescriptor.parse_obj(json.load(f))
                except Exception as e:
                    raise CLIRuntimeError(f"Cannot read processor descriptor at {descriptor_path}: {e}")

            # determine the image name
            repo = Repo(repo_path)
            username, repo_name = repo.remotes.origin.url.split("/")[-2:]
            repo_name = repo_name.rstrip(".git")
            commit_id = repo.head.commit.hexsha
            image_name = f"{username}/{repo_name}/{descriptor.name}:{commit_id}"
            print(f"Building image '{image_name}'. This may take a while...")

            # build the image
            image_existed = build_processor_image(processor_path, image_name, credentials=credentials,
                                                  force_build=args['force_build'])
            if args['force_build'] or not image_existed:
                print(f"Done building image '{image_name}'.")
            else:
                print(f"Using existing building image '{image_name}'.")

            dor = DORProxy(args['address'])
            if args['store_image']:
                # export the image
                export_path = os.path.join(tempdir, 'image.tar')
                docker_export_image(image_name, export_path, keep_image=image_existed or args['keep_image'])
                print(f"Done exporting image to '{export_path}'.")

                # upload the image to the DOR and set GPP tags
                pdi = dor.add_data_object(export_path, keystore.identity, False, False, 'ProcessorDockerImage', 'tar',
                                          tags=[
                                              DataObject.Tag(key='repository', value=args['repository']),
                                              DataObject.Tag(key='commit_id', value=args['commit_id']),
                                              DataObject.Tag(key='commit_timestamp', value=commit_timestamp),
                                              DataObject.Tag(key='proc_path', value=args['proc_path']),
                                              DataObject.Tag(key='proc_descriptor', value=descriptor.dict()),
                                              DataObject.Tag(key='image_name', value=image_name)
                                          ])
                print(f"Done uploading image to DOR -> object id: {pdi.obj_id}")
                os.remove(export_path)
                return {
                    'pdi': pdi
                }

            else:
                # store the GPP information in a file
                gpp_path = os.path.join(tempdir, 'gpp.json')
                with open(gpp_path, 'w') as f:
                    gpp = GitProcessorPointer(repository=args['repository'], commit_id=args['commit_id'],
                                              proc_path=args['proc_path'], proc_descriptor=descriptor)
                    json.dump(gpp.dict(), f)

                # upload the image to the DOR and set GPP tags
                pdi = dor.add_data_object(gpp_path, keystore.identity, False, False, 'ProcessorDockerImage', 'json',
                                          tags=[
                                              DataObject.Tag(key='repository', value=args['repository']),
                                              DataObject.Tag(key='commit_id', value=args['commit_id']),
                                              DataObject.Tag(key='commit_timestamp', value=commit_timestamp),
                                              DataObject.Tag(key='proc_path', value=args['proc_path']),
                                              DataObject.Tag(key='proc_descriptor', value=descriptor.dict()),
                                              DataObject.Tag(key='image_name', value=image_name)
                                          ])
                print(f"Done uploading PDI to DOR -> object id: {pdi.obj_id}")
                os.remove(gpp_path)
                return {
                    'pdi': pdi
                }
