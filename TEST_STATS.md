# Test Statistics

## Summary

- **Total tests:** 160
- **Total runtime:** ~19 minutes
- **Coverage:** 82% (target: 80%)

## Test Timings

| Test File | Tests | Time | Timeout |
|-----------|-------|------|---------|
| `test_unit_core.py` | 14 | 11s | 30s |
| `test_unit_helpers.py` | 31 | <1s | 30s |
| `test_cli_identity.py` | 3 | 12s | 30s |
| `test_cli_dor.py` | 3 | 11s | 30s |
| `test_cli_misc.py` | 2 | 8s | 30s |
| `test_cli_image.py` | 5 | 2m 25s | 5m |
| `test_cli_rti.py` | 5 | 52s | 2m |
| `test_cli_runner.py` | 14 | 46s | 2m |
| `test_dor.py` | 16 | 8s | 30s |
| `test_namespace.py` | 5 | 23s | 1m |
| `test_nodedb.py` | 13 | 56s | 2m |
| `test_p2p.py` | 7 | 23s | 1m |
| `test_rest.py` | 7 | 10s | 30s |
| `test_rti.py` | 19 | 10m 30s | 15m |
| `test_processor_abc.py` | 4 | 35s | 2m |
| `test_processor_ping.py` | 5 | 34s | 2m |
| `test_processor_primes.py` | 5 | 41s | 2m |
| `test_processor_cosim.py` | 2 | 31s | 1m |

## Coverage by Module

| Module | Coverage |
|--------|----------|
| `simaas/cli/` | 74-90% |
| `simaas/core/` | 84-100% |
| `simaas/dor/` | 88-100% |
| `simaas/node/` | 90-100% |
| `simaas/nodedb/` | 75-100% |
| `simaas/p2p/` | 78-100% |
| `simaas/rest/` | 75-97% |
| `simaas/rti/` | 83-100% |
| **Overall** | **82%** |

## Running Tests

```bash
# Run all tests
.venv/bin/python -m pytest simaas/tests/ -v

# Run specific category
.venv/bin/python -m pytest simaas/tests/test_unit_*.py -v
.venv/bin/python -m pytest simaas/tests/test_cli_*.py -v
.venv/bin/python -m pytest simaas/tests/test_processor_*.py -v

# Run with coverage
.venv/bin/python -m coverage run -m pytest simaas/tests/ -v
.venv/bin/python -m coverage report --ignore-errors --include="simaas/*" --omit="simaas/tests/*"
```

## Requirements

- Docker must be running for RTI and processor tests
- AWS credentials required for `@aws_only` marked tests
