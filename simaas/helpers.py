import inspect
import os
import socket
import sys
from importlib.util import spec_from_file_location, module_from_spec
from threading import Lock

import netifaces
import ipaddress
from typing import List, Optional, Tuple, Dict

import docker
from docker.models.containers import Container
from docker.models.images import Image
from simaas.core.logging import Logging

from simaas.core.processor import ProcessorBase
from simaas.rti.schemas import Task

logger = Logging.get(__name__)


def determine_local_ip() -> Optional[str]:
    # determine all private IPv4 addresses that are not loopback devices
    private_ipv4_addresses = []
    for interface in netifaces.interfaces():
        addresses = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addresses:
            for entry in addresses[netifaces.AF_INET]:
                ip_address = entry['addr']

                # check if the address is private and not a loopback device
                addr = ipaddress.IPv4Address(ip_address)
                if addr.is_private and not addr.is_loopback:
                    private_ipv4_addresses.append(ip_address)

    return private_ipv4_addresses[0] if private_ipv4_addresses else None


LOCAL_IP = determine_local_ip()


def determine_default_p2p_address() -> str:
    return f"tcp://{LOCAL_IP}:4001" if LOCAL_IP else "tcp://127.0.0.1:4001"


def determine_default_rest_address() -> str:
    return f"{LOCAL_IP}:5001" if LOCAL_IP else "127.0.0.1:5001"


def determine_default_ws_address() -> str:
    return f"{LOCAL_IP}:6001" if LOCAL_IP else "127.0.0.1:6001"


class PortMaster:
    _mutex = Lock()
    _next_p2p = {}
    _next_rest = {}
    _next_ws = {}

    @classmethod
    def generate_p2p_address(cls, host: str = '127.0.0.1', protocol: str = 'tcp') -> str:
        with cls._mutex:
            if host not in cls._next_p2p:
                cls._next_p2p[host] = 4100

            address = f"{protocol}://{host}:{cls._next_p2p[host]}"
            cls._next_p2p[host] += 1

            return address

    @classmethod
    def generate_rest_address(cls, host: str = '127.0.0.1') -> (str, int):
        with cls._mutex:
            if host not in cls._next_rest:
                cls._next_rest[host] = 5100

            address = (host, cls._next_rest[host])
            cls._next_rest[host] += 1
            return address

    @classmethod
    def generate_ws_address(cls, host: str = '127.0.0.1') -> (str, int):
        with cls._mutex:
            if host not in cls._next_ws:
                cls._next_ws[host] = 6100

            address = (host, cls._next_rest[host])
            cls._next_ws[host] += 1
            return address


def find_available_port(host: str = 'localhost', port_range: (int, int) = (6000, 7000)) -> Optional[int]:
    for port in range(port_range[0], port_range[1], 1):
        # create a socket object and set a timeout to avoid blocking indefinitely
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)

        # try to connect to the specified host and port
        try:
            sock.connect((host, port))
        except socket.error as e:
            if isinstance(e, (TimeoutError, ConnectionRefusedError)):
                return port

        finally:
            sock.close()

    return None


def docker_find_image(image_name: str) -> List[Image]:
    client = docker.from_env()
    available: List[Image] = client.images.list()
    result: List[Image] = []
    for image in available:
        for tag in image.tags:
            if image_name in tag:
                result.append(image)
    return result


def docker_delete_image(image_name: str) -> None:
    client = docker.from_env()
    image = client.images.get(image_name)
    client.images.remove(image.id, force=True)


def docker_export_image(image_name: str, output_path: str, keep_image: bool = True) -> None:
    client = docker.from_env()

    # save the docker image
    image = client.images.get(image_name)
    with open(output_path, 'wb') as f:
        for chunk in image.save(named=True):
            f.write(chunk)

    # delete the image (if applicable)
    if not keep_image:
        client.images.remove(image.id, force=True)


def docker_load_image(image_path: str, image_name: str, undo_if_no_match: bool = True) -> Optional[Image]:
    client = docker.from_env()
    with open(image_path, 'rb') as f:
        loaded_images = client.images.load(f.read())

        # does the image name match?
        found = None
        for image in loaded_images:
            if image_name in image.tags:
                found = image
                break

        # if not found, undo?
        if undo_if_no_match and not found:
            for image in loaded_images:
                client.images.remove(image.id, force=True)

        return found


def docker_run_job_container(image_name: str, p2p_address: Tuple[str, int],
                             custodian_address: str, custodian_pubkey: str, job_id: str,
                             budget: Optional[Task.Budget] = None) -> str:

    client = docker.from_env()

    volumes = {
        # job_path: {'bind': '/job', 'mode': 'rw'}
    }

    ports = {
        '6000/tcp': p2p_address,
    }

    environment = {
        'SIMAAS_CUSTODIAN_ADDRESS': custodian_address,
        'SIMAAS_CUSTODIAN_PUBKEY': custodian_pubkey,
        'JOB_ID': job_id,
        'EXTERNAL_P2P_ADDRESS': f"tcp://{p2p_address[0]}:{p2p_address[1]}"
    }

    if budget is None:
        container = client.containers.run(
            image=image_name,
            volumes=volumes,
            ports=ports,
            detach=True,
            stderr=True, stdout=True,
            auto_remove=False,
            environment=environment
        )
    else:
        # determine CPU quota
        cpu_period = 100000
        cpu_quota = cpu_period * budget.vcpus

        container = client.containers.run(
            image=image_name,
            volumes=volumes,
            ports=ports,
            detach=True,
            stderr=True, stdout=True,
            auto_remove=False,
            environment=environment,
            mem_limit=f"{budget.memory}m",
            cpu_period=cpu_period,
            cpu_quota=cpu_quota
        )

    return container.id


def docker_kill_job_container(container_id: str) -> None:
    client = docker.from_env()
    container = client.containers.get(container_id)
    container.kill()


def docker_delete_container(container_id: str) -> None:
    client = docker.from_env()
    container = client.containers.get(container_id)
    container.remove()


def docker_container_running(container_id: str) -> bool:
    client = docker.from_env()
    container = client.containers.get(container_id)
    return container.status == "running"


def docker_container_list() -> Dict[str, Container]:
    client = docker.from_env()
    return {
        container.id: container for container in client.containers.list()
    }


def docker_check_image_platform(image_name: str, platform: str) -> bool:
    client = docker.from_env()

    # extract OS and architecture
    os_ref, arch_ref = platform.split('/')

    # get the image and its OS and arch types
    image = client.images.get(image_name)
    os_type = image.attrs.get("Os", "")
    arch_type = image.attrs.get("Architecture", "")

    return os_type == os_ref and arch_type == arch_ref


def find_processors(search_path: str) -> Dict[str, ProcessorBase]:
    """
    Convenient function that finds all processor implementations in a given search path (including all its
    subdirectories) and returns a dictionary with the findings.

    :param search_path: the path to search for processors.
    :return: a dictionary that maps name of the processor to its implementation class.
    """
    sys.path.append(search_path)

    result = {}
    for root, dirs, files in os.walk(search_path):
        for file in files:
            print(file)
            if file == "processor.py":
                module_path = os.path.join(root, file)
                module_name = os.path.splitext(os.path.basename(module_path))[0]

                spec = spec_from_file_location(module_name, module_path)
                module = module_from_spec(spec)

                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    logger.warning(f"spec loader failed: {e}")
                    continue

                # module = importlib.import_module(module_name)
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and obj != ProcessorBase and issubclass(obj, ProcessorBase):
                        try:
                            instance: ProcessorBase = obj(root)
                            result[instance.name] = instance
                        except Exception as e:
                            logger.warning(f"creating instance of {obj} failed: {e}")

    return result
