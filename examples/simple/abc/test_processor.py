import os
import tempfile

from processor import read_value, write_value


def test_read_write_roundtrip():
    """write_value then read_value returns the original value."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        path = f.name
    try:
        for v in [-100, -1, 0, 1, 42, 999]:
            write_value(path, v)
            assert read_value(path) == v
    finally:
        os.remove(path)


def test_abc_sum():
    """a + b == c for known inputs."""
    with tempfile.TemporaryDirectory() as wd:
        write_value(os.path.join(wd, 'a'), 3)
        write_value(os.path.join(wd, 'b'), 5)

        a = read_value(os.path.join(wd, 'a'))
        b = read_value(os.path.join(wd, 'b'))

        write_value(os.path.join(wd, 'c'), a + b)
        assert read_value(os.path.join(wd, 'c')) == 8
