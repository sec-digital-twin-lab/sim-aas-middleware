"""Unit tests for CLI helper modules."""

import json
import os
import tempfile

import pytest

from simaas.cli.helpers.address import extract_address, ensure_address
from simaas.cli.helpers.output import shorten_id, table, print_result
from simaas.cli.helpers.time import parse_period
from simaas.cli.helpers.prompts import default_if_missing, deserialise_tag_value
from simaas.cli.helpers.storage import initialise_storage_folder
from simaas.core.errors import CLIError
from simaas.dor.schemas import DataObject


class TestExtractAddress:
    """Tests for extract_address function."""

    def test_valid_address(self):
        """Test parsing valid host:port address."""
        host, port = extract_address("localhost:5000")
        assert host == "localhost"
        assert port == 5000

    def test_valid_ip_address(self):
        """Test parsing IP address with port."""
        host, port = extract_address("192.168.1.1:8080")
        assert host == "192.168.1.1"
        assert port == 8080

    def test_invalid_no_colon(self):
        """Test that address without colon raises CLIError."""
        with pytest.raises(CLIError):
            extract_address("localhost5000")

    def test_invalid_multiple_colons(self):
        """Test that address with multiple colons raises CLIError."""
        with pytest.raises(CLIError):
            extract_address("localhost:5000:extra")

    def test_invalid_non_numeric_port(self):
        """Test that address with non-numeric port raises CLIError."""
        with pytest.raises(CLIError):
            extract_address("localhost:abc")


class TestShortenId:
    """Tests for shorten_id function."""

    def test_long_id(self):
        """Test shortening a standard long ID."""
        long_id = "1234567890abcdef1234567890abcdef"
        assert shorten_id(long_id) == "1234...cdef"

    def test_uuid_format(self):
        """Test shortening a UUID-like string."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert shorten_id(uuid) == "550e...0000"


class TestTable:
    """Tests for table function."""

    def test_basic_table(self):
        """Test creating a basic table with headers and rows."""
        headers = ["NAME", "VALUE"]
        rows = [["foo", "bar"], ["baz", "qux"]]
        result = table(headers, rows)
        assert "NAME" in result
        assert "VALUE" in result
        assert "foo" in result
        assert "bar" in result

    def test_empty_rows(self, capsys):
        """Test table with empty rows prints empty message."""
        headers = ["NAME", "VALUE"]
        rows = []
        result = table(headers, rows, empty_msg="No items found.")
        assert result is None
        captured = capsys.readouterr()
        assert "No items found." in captured.out

    def test_custom_empty_message(self, capsys):
        """Test custom empty message."""
        headers = ["COL1"]
        rows = []
        result = table(headers, rows, empty_msg="Custom empty message.")
        assert result is None
        captured = capsys.readouterr()
        assert "Custom empty message." in captured.out


class TestPrintResult:
    """Tests for print_result function."""

    def test_json_mode_dict(self, capsys):
        """Test JSON mode with a dictionary."""
        data = {"key": "value", "number": 42}
        print_result(data, json_mode=True)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["key"] == "value"
        assert output["number"] == 42

    def test_json_mode_list(self, capsys):
        """Test JSON mode with a list."""
        data = [{"id": 1}, {"id": 2}]
        print_result(data, json_mode=True)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert len(output) == 2
        assert output[0]["id"] == 1

    def test_empty_data(self, capsys):
        """Test printing empty data."""
        print_result([], json_mode=False, empty_msg="Nothing to show.")
        captured = capsys.readouterr()
        assert "Nothing to show." in captured.out


class TestParsePeriod:
    """Tests for parse_period function."""

    def test_hours(self):
        """Test parsing hours."""
        assert parse_period("24h") == 24
        assert parse_period("1h") == 1
        assert parse_period("48h") == 48

    def test_days(self):
        """Test parsing days."""
        assert parse_period("1d") == 24
        assert parse_period("7d") == 168
        assert parse_period("30d") == 720

    def test_weeks(self):
        """Test parsing weeks."""
        assert parse_period("1w") == 168
        assert parse_period("2w") == 336

    def test_case_insensitive(self):
        """Test that unit is case insensitive."""
        assert parse_period("24H") == 24
        assert parse_period("1D") == 24
        assert parse_period("1W") == 168

    def test_invalid_format(self):
        """Test that invalid formats return None."""
        assert parse_period("invalid") is None
        assert parse_period("") is None
        assert parse_period("24") is None
        assert parse_period("h") is None
        assert parse_period("24x") is None


class TestDefaultIfMissing:
    """Tests for default_if_missing function."""

    def test_missing_key(self):
        """Test that default is set when key is missing."""
        args = {}
        result = default_if_missing(args, "foo", "default_value")
        assert result == "default_value"
        assert args["foo"] == "default_value"

    def test_existing_key(self):
        """Test that existing value is preserved."""
        args = {"foo": "existing_value"}
        result = default_if_missing(args, "foo", "default_value")
        assert result == "existing_value"
        assert args["foo"] == "existing_value"

    def test_none_value(self):
        """Test that None value is replaced with default."""
        args = {"foo": None}
        result = default_if_missing(args, "foo", "default_value")
        assert result == "default_value"
        assert args["foo"] == "default_value"


class TestDeserialiseTagValue:
    """Tests for deserialise_tag_value function."""

    def test_integer_string(self):
        """Test converting integer string."""
        tag = DataObject.Tag(key="count", value="42")
        result = deserialise_tag_value(tag)
        assert result.value == 42
        assert isinstance(result.value, int)

    def test_float_string(self):
        """Test converting float string."""
        tag = DataObject.Tag(key="ratio", value="3.14")
        result = deserialise_tag_value(tag)
        assert result.value == 3.14
        assert isinstance(result.value, float)

    def test_boolean_true(self):
        """Test converting 'true' string."""
        tag = DataObject.Tag(key="enabled", value="true")
        result = deserialise_tag_value(tag)
        assert result.value is True

    def test_boolean_false(self):
        """Test converting 'false' string."""
        tag = DataObject.Tag(key="enabled", value="false")
        result = deserialise_tag_value(tag)
        assert result.value is False

    def test_json_object(self):
        """Test converting JSON object string."""
        tag = DataObject.Tag(key="config", value='{"nested": "value"}')
        result = deserialise_tag_value(tag)
        assert result.value == {"nested": "value"}

    def test_plain_string(self):
        """Test that plain strings are preserved."""
        tag = DataObject.Tag(key="name", value="just a string")
        result = deserialise_tag_value(tag)
        assert result.value == "just a string"


class TestInitialiseStorageFolder:
    """Tests for initialise_storage_folder function."""

    def test_creates_directory(self):
        """Test that directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = os.path.join(tmpdir, "new_folder")
            assert not os.path.exists(new_path)
            initialise_storage_folder(new_path, "test")
            assert os.path.isdir(new_path)

    def test_existing_directory(self):
        """Test that existing directory is not modified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            initialise_storage_folder(tmpdir, "test")
            assert os.path.isdir(tmpdir)

    def test_file_path_raises_error(self):
        """Test that file path raises CLIError."""
        with tempfile.NamedTemporaryFile() as tmpfile:
            with pytest.raises(CLIError):
                initialise_storage_folder(tmpfile.name, "test")
