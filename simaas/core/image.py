import os
import shutil
import subprocess
import tempfile
import traceback
from typing import Optional, Tuple, List

from git import Repo

from simaas.core.errors import CLIError
from simaas.core.logging import get_logger
from simaas.helpers import docker_client

log = get_logger('simaas.core', 'core')


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
