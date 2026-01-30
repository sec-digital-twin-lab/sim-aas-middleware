"""
Log code registry for structured logging.

Format: [CODE] human description: key=value key=value
Code format: SUBSYSTEM-OPERATION-SEQUENCE

Usage:
    from simaas.core.log_codes import LogCode, log_msg

    logger.info(log_msg(LogCode.P2P_CONN_001, "peer connected", peer=peer_id, addr=addr))
    # Output: [P2P-CONN-001] peer connected: peer=abc123 addr=192.168.1.1
"""

from enum import Enum
from typing import Any


class LogCode(str, Enum):
    """Registry of log codes for AI-parseable logging."""

    # P2P subsystem
    P2P_CONN_001 = "P2P-CONN-001"  # Peer connected
    P2P_CONN_002 = "P2P-CONN-002"  # Peer disconnected
    P2P_CONN_003 = "P2P-CONN-003"  # Connection failed
    P2P_SEND_001 = "P2P-SEND-001"  # Message sent
    P2P_RECV_001 = "P2P-RECV-001"  # Message received
    P2P_TIMEOUT_001 = "P2P-TIMEOUT-001"  # Operation timed out

    # DOR subsystem
    DOR_ADD_001 = "DOR-ADD-001"  # Object added
    DOR_FETCH_001 = "DOR-FETCH-001"  # Object fetched
    DOR_FETCH_002 = "DOR-FETCH-002"  # Object not found
    DOR_DELETE_001 = "DOR-DELETE-001"  # Object deleted

    # RTI subsystem
    RTI_DEPLOY_001 = "RTI-DEPLOY-001"  # Processor deployed
    RTI_DEPLOY_002 = "RTI-DEPLOY-002"  # Processor deploy failed
    RTI_UNDEPLOY_001 = "RTI-UNDEPLOY-001"  # Processor undeployed
    RTI_JOB_001 = "RTI-JOB-001"  # Job submitted
    RTI_JOB_002 = "RTI-JOB-002"  # Job completed
    RTI_JOB_003 = "RTI-JOB-003"  # Job failed
    RTI_JOB_004 = "RTI-JOB-004"  # Job timeout

    # Node subsystem
    NODE_INIT_001 = "NODE-INIT-001"  # Node initialized
    NODE_INIT_002 = "NODE-INIT-002"  # Node init failed
    NODE_JOIN_001 = "NODE-JOIN-001"  # Network joined
    NODE_JOIN_002 = "NODE-JOIN-002"  # Network join failed
    NODE_SHUTDOWN_001 = "NODE-SHUTDOWN-001"  # Node shutdown

    # Auth subsystem
    AUTH_OK_001 = "AUTH-OK-001"  # Authentication succeeded
    AUTH_FAIL_001 = "AUTH-FAIL-001"  # Authentication failed
    AUTH_DENY_001 = "AUTH-DENY-001"  # Authorisation denied

    # Config subsystem
    CFG_LOAD_001 = "CFG-LOAD-001"  # Config loaded
    CFG_ERR_001 = "CFG-ERR-001"  # Configuration error

    # REST subsystem
    REST_REQ_001 = "REST-REQ-001"  # Request received
    REST_ERR_001 = "REST-ERR-001"  # Request error

    # Namespace subsystem
    NS_RESERVE_001 = "NS-RESERVE-001"  # Resource reserved
    NS_CLAIM_001 = "NS-CLAIM-001"  # Resource claimed
    NS_RELEASE_001 = "NS-RELEASE-001"  # Resource released


def log_msg(code: LogCode, message: str, **kwargs: Any) -> str:
    """
    Format a log message with code prefix and key=value pairs.

    Args:
        code: The log code from LogCode enum
        message: Human-readable description
        **kwargs: Key-value pairs to append

    Returns:
        Formatted string: [CODE] message: key=value key=value

    Example:
        log_msg(LogCode.P2P_CONN_001, "peer connected", peer="abc", addr="1.2.3.4")
        # Returns: "[P2P-CONN-001] peer connected: peer=abc addr=1.2.3.4"
    """
    parts = [f"[{code.value}] {message}"]

    if kwargs:
        kv_pairs = " ".join(f"{k}={v}" for k, v in kwargs.items())
        parts.append(f": {kv_pairs}")

    return "".join(parts)
