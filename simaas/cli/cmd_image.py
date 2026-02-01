import getpass
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from InquirerPy.base import Choice
from dotenv import load_dotenv
from git import Repo, InvalidGitRepositoryError, NoSuchPathError
from pydantic import BaseModel

from simaas.core.errors import CLIError
from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_if_missing, load_keystore, \
    default_if_missing, use_env_or_prompt_if_missing, label_data_object, prompt_for_selection
from simaas.core.logging import get_logger
from simaas.dor.api import DORProxy
from simaas.dor.schemas import ProcessorDescriptor, DataObject, GitProcessorPointer
from simaas.helpers import docker_export_image, determine_default_rest_address, docker_local_arch, docker_client, \
    is_valid_new_file
from simaas.nodedb.api import NodeDBProxy

log = get_logger('simaas.cli', 'cli')


class PDIMetaInformation(BaseModel):
    proc_descriptor: ProcessorDescriptor
    proc_path: str
    repository: str
    commit_id: Optional[str]
    content_hash: str
    is_git_repo: bool
    is_dirty: bool
    image_name: str


def inspect_processor_path(proc_path: str) -> PDIMetaInformation:
    proc_path = Path(proc_path).resolve()

    # ------------------------------------------------------
    # 1) Check required files exist
    # ------------------------------------------------------
    missing = []
    for required in ['descriptor.json', 'processor.py', 'Dockerfile']:
        file_path = proc_path / required
        if not file_path.is_file():
            missing.append(required)

    if missing:
        raise CLIError(f"Processor folder is missing files: {missing}")

    # ------------------------------------------------------
    # 2) Read descriptor
    # ------------------------------------------------------
    descriptor_path = proc_path / 'descriptor.json'
    with open(descriptor_path, 'r') as f:
        try:
            descriptor = ProcessorDescriptor.model_validate(json.load(f))
        except Exception as e:
            raise CLIError(f"Cannot read processor descriptor at {descriptor_path}: {e}")

    # ------------------------------------------------------
    # 3) Calculate content hash
    # ------------------------------------------------------
    sha256 = hashlib.sha256()
    for root, _, files in sorted(os.walk(proc_path)):
        for file_name in sorted(files):
            file_path = os.path.join(root, file_name)

            # Include file path (relative to the root) in hash to account for structure
            rel_file_path = str(Path(file_path).relative_to(proc_path))
            sha256.update(rel_file_path.encode())

            # Read file content
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
    content_hash = sha256.hexdigest()
    print(f"SHA256 hash of processor contents: {content_hash}")

    # ------------------------------------------------------
    # 4) Git inspection
    # ------------------------------------------------------
    try:
        repo = Repo(proc_path, search_parent_directories=True)
        git_root = Path(repo.git.rev_parse("--show-toplevel"))
        rel_proc_path = str(proc_path.relative_to(git_root))

        # Remote URL
        repository = None
        if repo.remotes:
            try:
                repository = repo.remotes.origin.url
            except AttributeError:
                if len(repo.remotes) > 0:
                    repository = repo.remotes[0].url

            # Convert SSH to HTTPS if needed
            if repository and repository.startswith("git@") and ":" in repository:
                host, path = repository.split("@")[1].split(":", 1)
                repository = f"https://{host}/{path}"

        # remove the .git (if applicable)
        if repository.endswith('.git'):
            repository = repository[:-4]

        # Commit ID and dirty state
        commit_id = repo.head.commit.hexsha
        is_dirty = repo.is_dirty(untracked_files=True)

        return PDIMetaInformation(
            proc_descriptor=descriptor,
            proc_path=rel_proc_path,
            repository=repository,
            commit_id=commit_id,
            content_hash=content_hash,
            is_git_repo=True,
            is_dirty=is_dirty,
            image_name=f"{descriptor.name}:{content_hash}"
        )

    except (InvalidGitRepositoryError, NoSuchPathError):
        # determine the relative proc path
        home_dir = Path.home()
        rel_proc_path = str(proc_path.relative_to(home_dir))

        # Not a Git repo → synthetic URL
        username = getpass.getuser()
        repository = f"local://{username}@{rel_proc_path}"

        return PDIMetaInformation(
            proc_descriptor=descriptor,
            proc_path=rel_proc_path,
            repository=repository,
            commit_id=None,
            content_hash=content_hash,
            is_git_repo=False,
            is_dirty=False,
            image_name=f"{descriptor.name}:{content_hash}"
        )


def clone_repository(repository_url: str, repository_path: str, commit_id: str = None,
                     credentials: Optional[Tuple[str, str]] = None, simulate_only: bool = False) -> int:
    # if we don't simulate, we may to delete and actually clone the repo
    if not simulate_only:
        original_url = repository_url

        # do we have credentials? inject it into the repo URL
        if credentials:
            idx = repository_url.index('github.com')
            url0 = repository_url[:idx]
            url1 = repository_url[idx:]
            repository_url = f"{url0}{credentials[0]}:{credentials[1]}@{url1}"

        try:
            # does the destination already exist?
            try:
                shutil.rmtree(repository_path)
            except OSError as e:
                log.warning('clone', 'Failed to remove directory', path=repository_path, error=str(e))

            # clone the repo
            Repo.clone_from(repository_url, repository_path)

        except Exception as e:
            raise CLIError(reason=f"Failed to clone '{original_url}'", details={'exception': str(e)})

    try:
        # checkout a specific commit
        repo = Repo(repository_path)
        repo.git.checkout(commit_id)

        # determine the commit timestamp
        commit = repo.commit(commit_id)
        commit_timestamp = commit.authored_datetime.timestamp()

        return int(commit_timestamp)

    except Exception as e:
        raise CLIError(reason=f"Failed to checkout '{commit_id}'", details={'exception': str(e)})


def build_processor_image(processor_path: str, simaas_path: str, image_name: str, credentials: Tuple[str, str] = None,
                          force_build: bool = False, platform: Optional[str] = None) -> bool:
    # does the processor path exist?
    if not os.path.isdir(processor_path):
        raise CLIError(f"Processor path {processor_path} does not exist or not a directory")

    # check if the image already exists
    with docker_client() as client:
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
            raise CLIError("Deleting existing docker image failed.", details={
                'exception': e
            })

    # build the processor docker image
    if force_build or not image_existed:
        with tempfile.TemporaryDirectory() as tempdir:
            # copy the processor to the temp location (resolve to absolute path first to handle '.' correctly)
            context_name = os.path.basename(os.path.abspath(processor_path))
            context_path = os.path.join(tempdir, context_name)
            shutil.copytree(processor_path, context_path)

            # copy the sim-aas-middleware repo into the temp context location
            simaas_dst_path = os.path.join(context_path, 'sim-aas-middleware')
            shutil.copytree(simaas_path, simaas_dst_path)

            credentials_path = os.path.join(tempdir, "credentials")
            try:
                # assemble the command
                command: List[str] = ['docker', 'build', '--no-cache']
                if platform:
                    command.extend(['--platform', platform])
                if credentials:
                    # write the credentials to file (temporarily)
                    with open(credentials_path, 'w') as f:
                        f.write(f"{credentials[0]}:{credentials[1]}")

                    command.extend(['--secret', f'id=git_credentials,src={credentials_path}'])
                command.extend(['-t', image_name, '.'])

                env = os.environ.copy()
                env['DOCKER_BUILDKIT'] = '1'

                subprocess.run(command, cwd=context_path, check=True, capture_output=True, text=True, env=env)

            except subprocess.CalledProcessError as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(e.stderr)
                raise CLIError("Creating docker image failed", details={
                    'stdout': e.stdout,
                    'stderr': e.stderr,
                    'exception': str(e),
                    'trace': trace
                })

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(trace)
                raise CLIError(f"Creating docker image failed: {e}", details={
                    'exception': str(e),
                    'trace': trace
                })

            finally:
                if os.path.isfile(credentials_path):
                    os.remove(credentials_path)

    return image_existed


def build_pdi_file(args: dict) -> dict:
    # inspect the processor path
    pdi_meta: PDIMetaInformation = inspect_processor_path(args['proc_path'])

    # determine the final pdi_path
    if os.path.isdir(args['pdi_path']):
        args['pdi_path'] = os.path.join(args['pdi_path'], f"{pdi_meta.proc_descriptor.name}_{pdi_meta.content_hash}.pdi")
    print(f"Using PDI file destination at '{args['pdi_path']}'.")

    # determine image name
    image_name = f"{pdi_meta.proc_descriptor.name}:{pdi_meta.content_hash}"

    print(f"Begin building PDI '{image_name}'. This may take a while...")

    # create the (temporary) GPP descriptor file
    gpp: GitProcessorPointer = GitProcessorPointer(
        repository=pdi_meta.repository,
        commit_id=pdi_meta.commit_id if pdi_meta.commit_id else f"content_hash:{pdi_meta.content_hash}",
        proc_path=pdi_meta.proc_path,
        proc_descriptor=pdi_meta.proc_descriptor
    )
    gpp_path = os.path.join(args['proc_path'], 'gpp.json')
    with open(gpp_path, 'w') as f:
        json.dump(gpp.model_dump(), f, indent=2)

    # build the image
    image_existed = build_processor_image(
        args['proc_path'], args['simaas_repo_path'], image_name,
        force_build=args['force_build'], platform=args['arch']
    )
    if args['force_build'] or not image_existed:
        print(f"Done building docker image (forced build: {'YES' if args['force_build'] else 'NO'}).")
    else:
        print("Using existing docker image.")

    # delete the temporary GPP file
    os.remove(gpp_path)

    # export the docker image
    docker_export_image(image_name, args['pdi_path'], keep_image=image_existed or args['keep_docker_image'])
    print("Done exporting docker image.")

    # append meta information
    metadata: dict = pdi_meta.model_dump()
    if args.get('verbose', False):
        print(f"Appending PDI meta information: {json.dumps(metadata, indent=2)}")
    metadata: bytes = json.dumps(metadata).encode("utf-8")
    length = struct.pack(">I", len(metadata))  # 4-byte big-endian
    with open(args['pdi_path'], "ab") as f:
        f.write(metadata)
        f.write(MARKER)
        f.write(length)

    print("Done building PDI.")

    return {
        'pdi_path': args['pdi_path'],
        'pdi_meta': pdi_meta
    }


class PDIBuildLocal(CLICommand):
    default_force_build = False
    default_keep_image = True
    default_arch = docker_local_arch()

    def __init__(self):
        super().__init__('build-local', 'build a PDI file from local source', arguments=[
            Argument('--arch', dest='arch', action='store',
                     help=f"the architecture to be used for the image (default: {self.default_arch})"),
            Argument('--force-build', dest="force_build", action='store_const', const=True,
                     help="Force building a processor docker image even if one already exists."),
            Argument('--delete-docker-image', dest="keep_docker_image", action='store_const', const=False,
                     help="Deletes the newly created docker image after building the PDI - note: if an image with the "
                          "same name already existed, this flag will be ignored, effectively resulting in the existing "
                          "image being replaced with the newly created one."),
            Argument('--simaas-repo-path', dest='simaas_repo_path', action='store',
                     help="Path to the sim-aas-middleware repository used for building PDIs."),
            Argument('--verbose', dest="verbose", action='store_const', const=True,
                     help="Show additional information during build process."),
            Argument('proc_path', metavar='proc_path', type=str, nargs=1,
                     help="the path to the directory of the processor"),
            Argument('pdi_path', metavar='pdi_path', type=str, nargs='?',
                     help=f"the path to store the PDI file (default: {os.path.join(Path.cwd(), '<image_name>.pdi')})")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        load_dotenv()

        # determine sim-aas-middleware path
        use_env_or_prompt_if_missing(args, 'simaas_repo_path', 'SIMAAS_REPO_PATH', prompt_for_string,
                                     message="Enter the path to the sim-aas-middleware repository")

        # set defaults if necessary
        default_if_missing(args, 'force_build', self.default_force_build)
        default_if_missing(args, 'keep_docker_image', self.default_keep_image)
        default_if_missing(args, 'arch', self.default_arch)

        # check the proc_path
        if isinstance(args['proc_path'], list) and args['proc_path']:
            args['proc_path'] = args['proc_path'][0]
        if not args['proc_path']:
            raise CLIError("Processor path missing")
        elif not os.path.isdir(args['proc_path']):
            raise CLIError(f"Processor path not found or not a directory: {args['proc_path']}")

        # check the pdi_path
        pdi_path = args['pdi_path'][0] if isinstance(args['pdi_path'], list) and args['pdi_path'] else args['pdi_path']
        pdi_path = Path(pdi_path).expanduser() if pdi_path else Path.cwd()
        if pdi_path.is_dir():
            pdi_path = pdi_path.resolve()
        elif pdi_path.is_file():
            raise CLIError(f"PDI path invalid: {pdi_path} (file already exists at this location)")
        elif is_valid_new_file(pdi_path):
            pdi_path = pdi_path.parent.resolve() / pdi_path.name
        else:
            raise CLIError(f"PDI path invalid: {pdi_path} (invalid file name or location)")
        args['pdi_path'] = str(pdi_path)

        return build_pdi_file(args)


class PDIBuildGithub(CLICommand):
    default_force_build = False
    default_keep_image = True
    default_arch = docker_local_arch()

    def __init__(self):
        super().__init__('build-github', 'build a PDI file from Github source', arguments=[
            Argument('--repository', dest='repository', action='store', help="URL of the repository"),
            Argument('--commit-id', dest='commit_id', action='store', help="the commit id"),
            Argument('--proc-path', dest='proc_path', action='store', help="path to the processor"),
            Argument('--git-username', dest='git_username', action='store', help="GitHub username"),
            Argument('--git-token', dest='git_token', action='store', help="GitHub personal access token"),
            Argument('--arch', dest='arch', action='store',
                     help=f"the architecture to be used for the image (default: {self.default_arch})"),
            Argument('--force-build', dest="force_build", action='store_const', const=True,
                     help="Force building a processor docker image even if one already exists."),
            Argument('--delete-docker-image', dest="keep_docker_image", action='store_const', const=False,
                     help="Deletes the newly created docker image after building the PDI - note: if an image with the "
                          "same name already existed, this flag will be ignored, effectively resulting in the existing "
                          "image being replaced with the newly created one."),
            Argument('--simaas-repo-path', dest='simaas_repo_path', action='store',
                     help="Path to the sim-aas-middleware repository used for building PDIs."),
            Argument('--verbose', dest="verbose", action='store_const', const=True,
                     help="Show additional information during build process."),
            Argument('pdi_path', metavar='pdi_path', type=str, nargs='?',
                     help=f"the path to store the PDI file (default: {os.path.join(Path.cwd(), '<image_name>.pdi')})")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        load_dotenv()

        # determine sim-aas-middleware path
        use_env_or_prompt_if_missing(args, 'simaas_repo_path', 'SIMAAS_REPO_PATH', prompt_for_string,
                                     message="Enter the path to the sim-aas-middleware repository")

        # set defaults if necessary
        default_if_missing(args, 'force_build', self.default_force_build)
        default_if_missing(args, 'keep_docker_image', self.default_keep_image)
        default_if_missing(args, 'arch', self.default_arch)

        # check the pdi_path
        pdi_path = args['pdi_path'][0] if isinstance(args['pdi_path'], list) and args['pdi_path'] else args['pdi_path']
        pdi_path = Path(pdi_path).expanduser() if pdi_path else Path.cwd()
        if pdi_path.is_dir():
            pdi_path = pdi_path.resolve()
        elif pdi_path.is_file():
            raise CLIError(f"PDI path invalid: {pdi_path} (file already exists at this location)")
        elif is_valid_new_file(pdi_path):
            pdi_path = pdi_path.parent.resolve() / pdi_path.name
        else:
            raise CLIError(f"PDI path invalid: {pdi_path} (invalid file name or location)")
        args['pdi_path'] = str(pdi_path)

        prompt_if_missing(args, 'repository', prompt_for_string, message="Enter URL of the repository:")
        prompt_if_missing(args, 'commit_id', prompt_for_string, message="Enter the commit id:")
        prompt_if_missing(args, 'proc_path', prompt_for_string, message="Enter path to the processor:")

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

        with tempfile.TemporaryDirectory() as repo_path:
            # clone the repository and checkout the specified commit
            clone_repository(args['repository'], repo_path, commit_id=args['commit_id'], credentials=credentials)
            print(f"Done cloning {args['repository']}.")

            # check the proc_path
            args['proc_path'] = os.path.join(repo_path, args['proc_path'])
            if not os.path.isdir(args['proc_path']):
                raise CLIError(f"Processor path not found or not a directory: {args['proc_path']}")

            return build_pdi_file(args)


MARKER = b"PDI_META"
MARKER_LEN = len(MARKER)
LEN_SIZE = 4  # 4-byte length field


class PDIImport(CLICommand):
    def __init__(self):
        super().__init__('import', 'import a PDI file to DOR', arguments=[
            Argument('--address', dest='address', action='store',
                     help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')"),
            Argument('pdi_path', metavar='pdi_path', type=str, nargs=1,
                     help="the path to the PDI file")
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
            raise CLIError(f"Node at {args['address']} does not support DOR capabilities")

        # check if the pdi_path exists
        if isinstance(args['pdi_path'], list) and args['pdi_path']:
            args['pdi_path'] = args['pdi_path'][0]
        if not os.path.isfile(args['pdi_path']):
            raise CLIError(f"Invalid pdi_path ('{args['pdi_path']}' does not exist or not a file)")

        path = Path(args['pdi_path'])
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        with path.open('rb') as f:
            # read last 4 bytes (length)
            f.seek(-LEN_SIZE, 2)
            length_bytes = f.read(LEN_SIZE)
            length = struct.unpack(">I", length_bytes)[0]

            # read marker
            f.seek(-(LEN_SIZE + MARKER_LEN), 2)
            marker = f.read(MARKER_LEN)
            if marker != MARKER:
                raise CLIError("Invalid PDI file (no marker found)")

            # read metadata
            f.seek(-(LEN_SIZE + MARKER_LEN + length), 2)
            meta_bytes = f.read(length)
            meta: dict = json.loads(meta_bytes.decode('utf-8'))
            try:
                PDIMetaInformation.model_validate(meta)
            except Exception:
                raise CLIError(f"Invalid PDI meta information: {json.dumps(meta, indent=2)}")

            # create temp file with only the original data
            data_size = path.stat().st_size - (length + MARKER_LEN + LEN_SIZE)
            f.seek(0)
            chunk_size = 8 * 1024 * 1024  # 8 MB
            remaining = data_size
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                tmp_file.write(f.read(to_read))
                remaining -= to_read
            tmp_file.close()

        # build tags from PDI meta information
        tags = [
            DataObject.Tag(key=key, value=value) for key, value in meta.items()
        ]

        # upload the image to the DOR and set GPP tags
        dor = DORProxy(args['address'])
        pdi = dor.add_data_object(
            tmp_file.name, keystore.identity, False, False, 'ProcessorDockerImage', 'tar', tags=tags
        )

        print(f"Importing PDI at {args['pdi_path']}' done -> object id: {pdi.obj_id}")
        os.remove(tmp_file.name)

        return {
            'pdi': pdi
        }


class PDIExport(CLICommand):
    def __init__(self):
        super().__init__('export', 'export a PDI file from DOR', arguments=[
            Argument('--address', dest='address', action='store',
                     help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')"),
            Argument('--obj-id', dest='obj_id', action='store',
                     help="the data object it of the PDI"),
            Argument('pdi_path', metavar='pdi_path', type=str, nargs=1,
                     help="the destination of the PDI file")
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
            raise CLIError(f"Node at {args['address']} does not support DOR capabilities")

        # get a list of all PDIs for that DOR
        dor = DORProxy(args['address'])
        result: List[DataObject] = dor.search(data_type='ProcessorDockerImage', data_format='tar')
        if not result:
            raise CLIError(f"No PDI objects found in DOR at {args['address']}")

        # allow the user to select a PDI if non specified
        if not args.get('obj_id'):
            choices = [Choice(item.obj_id, label_data_object(item)) for item in result]
            args['obj_id'] = prompt_for_selection(choices, "Select PDI for export", False)

        # check if the obj id is valid / can be found in DOR
        result: Dict[str, DataObject] = {item.obj_id: item for item in result}
        if args['obj_id'] not in result:
            raise CLIError(f"PDI object '{args['obj_id']}' not found.")
        meta: DataObject = result[args['obj_id']]

        # check if the destination already is a valid directory or file path
        if isinstance(args['pdi_path'], list) and args['pdi_path']:
            args['pdi_path'] = args['pdi_path'][0]
        pdi_path = os.path.abspath(args['pdi_path'])
        if os.path.isdir(pdi_path):
            pdi_path = os.path.join(pdi_path, f"{meta.tags['proc_descriptor']['name']}_{meta.tags['content_hash']}.pdi")
        elif os.path.isfile(pdi_path):
            raise CLIError(f"Invalid pdi_path '{args['pdi_path']}' (file already exists)")
        elif not is_valid_new_file(pdi_path):
            raise CLIError(f"Invalid pdi_path '{args['pdi_path']}'")

        # download the PDI
        dor.get_content(args['obj_id'], with_authorisation_by=keystore, download_path=pdi_path)

        # append meta information
        meta: dict = {
            'proc_descriptor': meta.tags['proc_descriptor'],
            'proc_path': meta.tags['proc_path'],
            'repository': meta.tags['repository'],
            'commit_id': meta.tags['commit_id'],
            'content_hash': meta.tags['content_hash'],
            'is_git_repo': meta.tags['is_git_repo'],
            'is_dirty': meta.tags['is_dirty'],
            'image_name': meta.tags['image_name']
        }
        pdi_meta: PDIMetaInformation = PDIMetaInformation.model_validate(meta)
        metadata = json.dumps(meta).encode("utf-8")
        length = struct.pack(">I", len(metadata))  # 4-byte big-endian
        with open(pdi_path, "ab") as f:
            f.write(metadata)
            f.write(MARKER)
            f.write(length)

        print(f"Exporting PDI (object id: {args['obj_id']}) done -> path: {pdi_path}.")

        return {
            'pdi_path': pdi_path,
            'pdi_meta': pdi_meta
        }
