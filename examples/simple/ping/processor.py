import json
import logging
import os
import socket
import subprocess
import time
from typing import Dict, List

from simaas.rti.schemas import Job

from simaas.core.processor import ProcessorBase, ProgressListener, Namespace


def ping_address(address: str, count=4) -> str:
    result = subprocess.run(
        ["ping", "-c", str(count), address],
        capture_output=True,
        text=True
    )
    return result.stdout


def traceroute_address(address: str, max_hops=30) -> str:
    result = subprocess.run(
        ["traceroute", "-m", str(max_hops), address],
        capture_output=True,
        text=True
    )
    return result.stdout


def tcp_connect(address: str, port: int, timeout: int = 5) -> Dict[str, any]:
    result = {
        'success': False,
        'error': None,
        'response_time_ms': None
    }
    
    try:
        start_time = time.time()
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        connection_result = sock.connect_ex((address, port))
        
        if connection_result == 0:
            result['success'] = True
            end_time = time.time()
            result['response_time_ms'] = round((end_time - start_time) * 1000, 2)
        else:
            result['error'] = f"Connection failed with error code: {connection_result}"
            
        sock.close()
        
    except socket.gaierror as e:
        result['error'] = f"Name resolution failed: {e}"
    except socket.timeout:
        result['error'] = f"Connection timeout after {timeout} seconds"
    except Exception as e:
        result['error'] = f"Unexpected error: {e}"
    
    return result


def udp_connect(address: str, port: int, timeout: int = 5) -> Dict[str, any]:
    result = {
        'success': False,
        'error': None,
        'response_time_ms': None
    }
    
    try:
        start_time = time.time()
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        
        # Send a simple test message
        test_message = b"connectivity_test"
        sock.sendto(test_message, (address, port))
        
        # Try to receive a response
        try:
            response, addr = sock.recvfrom(1024)
            end_time = time.time()
            result['success'] = True
            result['response_time_ms'] = round((end_time - start_time) * 1000, 2)
        except socket.timeout:
            # For UDP, timeout doesn't necessarily mean failure since UDP is connectionless
            # We'll consider it a partial success if we could send the packet
            end_time = time.time()
            result['success'] = True
            result['response_time_ms'] = round((end_time - start_time) * 1000, 2)
            result['note'] = "UDP packet sent successfully (no response received, but this is normal for UDP)"
            
        sock.close()
        
    except socket.gaierror as e:
        result['error'] = f"Name resolution failed: {e}"
    except Exception as e:
        result['error'] = f"Unexpected error: {e}"
    
    return result


class ProcessorPing(ProcessorBase):
    def __init__(self, proc_path: str) -> None:
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(
            self,
            wd_path: str,
            job: Job,
            listener: ProgressListener,
            namespace: Namespace,
            logger: logging.Logger
    ) -> None:
        # Initial progress update
        listener.on_progress_update(0)

        # Read the parameters file
        with open(os.path.join(wd_path, 'parameters'), 'r') as f:
            parameters = json.load(f)
        logger.info(f"Using parameters: {parameters}")

        # Get the address
        address: str = parameters['address']

        result: Dict[str, List[str]] = {}
        if parameters['do_ping']:
            # Do the ping
            stdout: str = ping_address(address)
            logger.info(f"Ping stdout: {stdout}")
            result['ping_stdout'] = stdout.split('\n')

        if parameters['do_traceroute']:
            stdout: str = traceroute_address(address, max_hops=5)
            logger.info(f"Traceroute stdout: {stdout}")
            result['traceroute_stdout'] = stdout.split('\n')

        if parameters.get('do_tcp_test', False):
            tcp_port = parameters.get('tcp_port', 80)
            tcp_timeout = parameters.get('tcp_timeout', 5)
            tcp_result = tcp_connect(address, tcp_port, tcp_timeout)
            logger.info(f"TCP connection test result: {tcp_result}")
            result['tcp_test'] = tcp_result

        if parameters.get('do_udp_test', False):
            udp_port = parameters.get('udp_port', 53)
            udp_timeout = parameters.get('udp_timeout', 5)
            udp_result = udp_connect(address, udp_port, udp_timeout)
            logger.info(f"UDP connection test result: {udp_result}")
            result['udp_test'] = udp_result

        # Write result
        with open(os.path.join(wd_path, 'result'), 'w') as f:
            json.dump(result, f, indent=2)

        listener.on_output_available('result')
        listener.on_progress_update(100)

    def interrupt(self) -> None:
        self._is_cancelled = True
