"""Unit tests for simaas.core.log_codes module."""

import pytest

from simaas.core.log_codes import LogCode, log_msg


class TestLogCode:
    """Tests for LogCode enum."""

    def test_code_format(self):
        """Test that codes follow SUBSYSTEM-OPERATION-SEQUENCE format."""
        for code in LogCode:
            parts = code.value.split("-")
            assert len(parts) == 3, f"{code.value} should have 3 parts"
            assert parts[0].isupper(), f"{code.value} subsystem should be uppercase"
            assert parts[1].isupper(), f"{code.value} operation should be uppercase"
            assert parts[2].isdigit(), f"{code.value} sequence should be numeric"

    def test_code_is_string(self):
        """Test that LogCode values are strings."""
        assert LogCode.P2P_CONN_001.value == "P2P-CONN-001"
        assert isinstance(LogCode.P2P_CONN_001.value, str)

    def test_all_subsystems_present(self):
        """Test that all expected subsystems have codes."""
        codes = [c.value for c in LogCode]
        subsystems = set(c.split("-")[0] for c in codes)

        expected = {"P2P", "DOR", "RTI", "NODE", "AUTH", "CFG", "REST", "NS"}
        assert expected.issubset(subsystems)


class TestLogMsg:
    """Tests for log_msg helper function."""

    def test_basic_format(self):
        """Test basic message formatting without kwargs."""
        result = log_msg(LogCode.P2P_CONN_001, "peer connected")

        assert result == "[P2P-CONN-001] peer connected"

    def test_with_single_kwarg(self):
        """Test message with single key-value pair."""
        result = log_msg(LogCode.P2P_CONN_001, "peer connected", peer="abc123")

        assert result == "[P2P-CONN-001] peer connected: peer=abc123"

    def test_with_multiple_kwargs(self):
        """Test message with multiple key-value pairs."""
        result = log_msg(
            LogCode.P2P_CONN_001,
            "peer connected",
            peer="abc123",
            addr="192.168.1.1"
        )

        assert "[P2P-CONN-001] peer connected:" in result
        assert "peer=abc123" in result
        assert "addr=192.168.1.1" in result

    def test_with_numeric_values(self):
        """Test message with numeric values."""
        result = log_msg(
            LogCode.RTI_JOB_004,
            "job timeout",
            job="job123",
            elapsed_ms=5000
        )

        assert "[RTI-JOB-004] job timeout:" in result
        assert "elapsed_ms=5000" in result

    def test_with_none_value(self):
        """Test message with None value."""
        result = log_msg(LogCode.DOR_FETCH_002, "object not found", obj="abc", peer=None)

        assert "peer=None" in result

    def test_empty_message(self):
        """Test with empty message string."""
        result = log_msg(LogCode.NODE_INIT_001, "")

        assert result == "[NODE-INIT-001] "

    def test_message_with_special_characters(self):
        """Test message with special characters."""
        result = log_msg(LogCode.CFG_ERR_001, "config error: missing 'key'")

        assert result == "[CFG-ERR-001] config error: missing 'key'"

    def test_kwarg_with_spaces_in_value(self):
        """Test that values with spaces are included as-is."""
        result = log_msg(LogCode.AUTH_FAIL_001, "auth failed", reason="invalid token format")

        assert "reason=invalid token format" in result


class TestLogMsgIntegration:
    """Integration tests for log_msg with actual logging."""

    def test_with_logger(self, caplog):
        """Test that log_msg works with Python logging."""
        import logging

        logger = logging.getLogger("test")
        logger.setLevel(logging.INFO)

        with caplog.at_level(logging.INFO):
            logger.info(log_msg(LogCode.DOR_ADD_001, "object added", obj="abc123"))

        assert "[DOR-ADD-001] object added: obj=abc123" in caplog.text

    def test_regex_extraction(self):
        """Test that code can be extracted via regex."""
        import re

        msg = log_msg(LogCode.P2P_SEND_001, "message sent", dest="peer1", size=1024)
        # Pattern allows alphanumeric subsystem (e.g., P2P, RTI)
        pattern = r"^\[([A-Z0-9]+-[A-Z]+-\d+)\]"

        match = re.match(pattern, msg)
        assert match is not None
        assert match.group(1) == "P2P-SEND-001"
