"""SSH tunnel connectivity tests.

These tests verify that the SSH tunnel is working correctly for AWS RTI tests.
The tunnel forwards ports from the EC2 instance to the local machine, allowing
AWS Batch jobs to connect back to the local custodian.

Requirements:
- SSH_TUNNEL_HOST: EC2 hostname/IP
- SSH_TUNNEL_USER: SSH username
- SSH_TUNNEL_KEY_PATH: Path to SSH private key
- GatewayPorts yes in /etc/ssh/sshd_config on the EC2 instance
"""

import os
import subprocess
import time

import pytest


def get_ssh_config():
    """Get SSH tunnel configuration from environment."""
    ssh_host = os.environ.get("SSH_TUNNEL_HOST")
    ssh_user = os.environ.get("SSH_TUNNEL_USER")
    ssh_key_path = os.environ.get("SSH_TUNNEL_KEY_PATH")
    return ssh_host, ssh_user, ssh_key_path


def check_ssh_config():
    """Check if SSH configuration is available."""
    ssh_host, ssh_user, ssh_key_path = get_ssh_config()
    return all([ssh_host, ssh_user, ssh_key_path])


# Skip entire module if SSH tunnel env vars are not configured
pytestmark = pytest.mark.skipif(
    not check_ssh_config(),
    reason="SSH tunnel env vars not configured (SSH_TUNNEL_HOST, SSH_TUNNEL_USER, SSH_TUNNEL_KEY_PATH)"
)


@pytest.fixture(scope="module")
def ssh_tunnel_for_test():
    """Set up SSH tunnel for testing."""
    ssh_host, ssh_user, ssh_key_path = get_ssh_config()

    if not all([ssh_host, ssh_user, ssh_key_path]):
        pytest.skip("SSH tunnel credentials not configured")

    # Use a test port to avoid conflicts
    test_port = 19999

    ssh_command = [
        "ssh",
        "-N",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-R", f"0.0.0.0:{test_port}:localhost:{test_port}",
        "-i", ssh_key_path,
        f"{ssh_user}@{ssh_host}"
    ]

    process = subprocess.Popen(
        ssh_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Give tunnel time to establish
    time.sleep(3)

    # Check if process is still running
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        pytest.fail(f"SSH tunnel failed to start: {stderr.decode()}")

    yield {
        'process': process,
        'host': ssh_host,
        'user': ssh_user,
        'port': test_port
    }

    process.terminate()
    process.wait(timeout=5)


@pytest.mark.integration
def test_ssh_tunnel_basic_connectivity(ssh_tunnel_for_test):
    """Test that we can SSH into the tunnel host."""
    ssh_host, ssh_user, ssh_key_path = get_ssh_config()

    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            "-i", ssh_key_path,
            f"{ssh_user}@{ssh_host}",
            "echo", "tunnel_test_ok"
        ],
        capture_output=True,
        text=True,
        timeout=30
    )

    assert result.returncode == 0, f"SSH connection failed: {result.stderr}"
    assert "tunnel_test_ok" in result.stdout


@pytest.mark.integration
def test_ssh_tunnel_port_binding(ssh_tunnel_for_test):
    """Test that the tunnel port is listening on the remote host.

    This verifies that GatewayPorts is enabled on the SSH server.
    If GatewayPorts is 'no', the port only binds to 127.0.0.1.
    If GatewayPorts is 'yes', the port binds to 0.0.0.0.
    """
    ssh_host, ssh_user, ssh_key_path = get_ssh_config()
    test_port = ssh_tunnel_for_test['port']

    # Check what interfaces the port is bound to on the remote
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            "-i", ssh_key_path,
            f"{ssh_user}@{ssh_host}",
            "ss", "-tln", f"sport = :{test_port}"
        ],
        capture_output=True,
        text=True,
        timeout=30
    )

    assert result.returncode == 0, f"Failed to check port binding: {result.stderr}"

    output = result.stdout
    print(f"Port binding output:\n{output}")

    # Check if port is bound to 0.0.0.0 (all interfaces) or just 127.0.0.1
    if f"0.0.0.0:{test_port}" in output or f"*:{test_port}" in output:
        print(f"Port {test_port} is bound to all interfaces (0.0.0.0) - GatewayPorts is enabled")
    elif f"127.0.0.1:{test_port}" in output:
        pytest.fail(
            f"Port {test_port} is only bound to 127.0.0.1 (localhost).\n"
            "This means GatewayPorts is disabled in /etc/ssh/sshd_config.\n"
            "AWS jobs won't be able to connect through the tunnel.\n\n"
            "To fix, on the EC2 instance:\n"
            "1. Add 'GatewayPorts yes' to /etc/ssh/sshd_config\n"
            "2. Run: sudo systemctl restart sshd"
        )
    else:
        pytest.fail(f"Port {test_port} doesn't appear to be listening. Output:\n{output}")


@pytest.mark.integration
def test_ssh_tunnel_tcp_connectivity(ssh_tunnel_for_test):
    """Test TCP connectivity through the SSH tunnel using netcat.

    This simulates what happens during the RTI handshake:
    1. Start a TCP server locally
    2. Connect to it through the tunnel (simulating AWS job)
    3. Verify bidirectional communication
    """
    ssh_host, ssh_user, ssh_key_path = get_ssh_config()
    test_port = ssh_tunnel_for_test['port']

    # Start local TCP server using netcat
    server_proc = subprocess.Popen(
        ["nc", "-l", str(test_port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    time.sleep(1)  # Give server time to start

    try:
        # From the EC2 host, connect to localhost (via tunnel) and send data
        result = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-i", ssh_key_path,
                f"{ssh_user}@{ssh_host}",
                f"printf 'HELLO_FROM_EC2\\n' | nc -w 5 127.0.0.1 {test_port}"
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        # Read what the server received
        try:
            stdout, stderr = server_proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            stdout, stderr = server_proc.communicate()

        print(f"SSH returncode: {result.returncode}")
        print(f"SSH stdout: {result.stdout}")
        print(f"SSH stderr: {result.stderr}")
        print(f"Server received: {stdout}")

        assert stdout and b"HELLO_FROM_EC2" in stdout, \
            f"Server didn't receive expected data. Got: {stdout}"

    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server_proc.kill()


@pytest.mark.integration
def test_ssh_tunnel_external_connectivity(ssh_tunnel_for_test):
    """Test that the tunnel is accessible from outside localhost on EC2.

    This is the critical test - it verifies that AWS jobs running on
    different hosts can reach the tunneled port via the EC2's hostname.
    """
    ssh_host, ssh_user, ssh_key_path = get_ssh_config()
    test_port = ssh_tunnel_for_test['port']

    # Get the EC2's internal hostname
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-i", ssh_key_path,
            f"{ssh_user}@{ssh_host}",
            "hostname", "-f"
        ],
        capture_output=True,
        text=True,
        timeout=30
    )

    assert result.returncode == 0, f"Failed to get hostname: {result.stderr}"
    ec2_internal_hostname = result.stdout.strip()
    print(f"EC2 internal hostname: {ec2_internal_hostname}")

    # Start local TCP server
    server_proc = subprocess.Popen(
        ["nc", "-l", str(test_port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    time.sleep(1)  # Give server time to start

    try:
        # From EC2, connect using the internal hostname (not localhost)
        # This is what AWS jobs would use
        result = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-i", ssh_key_path,
                f"{ssh_user}@{ssh_host}",
                f"printf 'HELLO_VIA_HOSTNAME\\n' | nc -w 5 {ec2_internal_hostname} {test_port}"
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        # Read what the server received
        try:
            stdout, stderr = server_proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            stdout, stderr = server_proc.communicate()

        print(f"SSH returncode: {result.returncode}")
        print(f"SSH stdout: {result.stdout}")
        print(f"SSH stderr: {result.stderr}")
        print(f"Server received: {stdout}")

        if not stdout or b"HELLO_VIA_HOSTNAME" not in stdout:
            pytest.fail(
                f"External connectivity test failed.\n"
                f"This likely means GatewayPorts is not enabled on the SSH server.\n"
                f"AWS jobs won't be able to connect to tcp://{ec2_internal_hostname}:{test_port}\n\n"
                f"Server received: {stdout}\n"
                f"SSH stderr: {result.stderr}\n\n"
                f"To fix, on the EC2 instance:\n"
                f"1. Add 'GatewayPorts yes' to /etc/ssh/sshd_config\n"
                f"2. Run: sudo systemctl restart sshd"
            )

    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server_proc.kill()


if __name__ == "__main__":
    # Quick manual test
    if not check_ssh_config():
        print("SSH tunnel not configured. Set these environment variables:")
        print("  SSH_TUNNEL_HOST")
        print("  SSH_TUNNEL_USER")
        print("  SSH_TUNNEL_KEY_PATH")
    else:
        print("SSH config found, running tests...")
        pytest.main([__file__, "-v"])
