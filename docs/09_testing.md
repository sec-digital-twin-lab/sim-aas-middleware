# Testing

This guide covers how to set up and run the test suite.

## Prerequisites

- Python 3.13+
- Docker (running)
- Development dependencies installed: `pip install ".[dev]"`

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SIMAAS_REPO_PATH` | Yes (for Docker RTI tests) | Absolute path to the middleware repository root. Used when building PDIs. |
| `GITHUB_USERNAME` | No | GitHub username for building PDIs from private repos. |
| `GITHUB_TOKEN` | No | GitHub personal access token for private repo access. |
| `AWS_ACCESS_KEY_ID` | No | For AWS RTI tests. |
| `AWS_SECRET_ACCESS_KEY` | No | For AWS RTI tests. |
| `AWS_DEFAULT_REGION` | No | For AWS RTI tests. |

Set `SIMAAS_REPO_PATH` before running tests:

```bash
export SIMAAS_REPO_PATH=$(pwd)
```

## Running Tests

Run the full suite:

```bash
source .venv/bin/activate
python -m pytest simaas/tests/ -v
```

Run a specific test file:

```bash
python -m pytest simaas/tests/test_unit_core.py -v
```

Run a specific test function:

```bash
python -m pytest simaas/tests/test_rti.py::test_job_submit_and_retrieve -v
```

Filter by marker:

```bash
python -m pytest simaas/tests/ -k docker -v     # only Docker tests
python -m pytest simaas/tests/ -m "not aws_only" -v  # skip AWS tests
```

## Test Waves

Tests are organized into waves, ordered from lightest/fastest to heaviest/slowest. Run lighter waves first to catch issues early.

| Wave | Tests | What it covers |
|------|-------|---------------|
| **0** | `test_cli_image.py` | PDI image builds (rebuilds all images from scratch). Opt-in; skip to reuse cached images. |
| **1** | `test_unit_core.py`, `test_unit_helpers.py`, `test_errors.py`, `test_logging.py` | Unit tests for core utilities, helpers, error handling, and logging. |
| **2** | `test_rest.py`, `test_rest_errors.py`, `test_cli_misc.py`, `test_cli_identity.py` | REST API endpoints, error responses, CLI commands. |
| **3** | `test_p2p.py` | P2P encrypted communication. |
| **4** | `test_namespace.py` | Namespace API and resource management. |
| **5** | `test_nodedb.py`, `test_dor.py`, `test_cli_dor.py` | NodeDB operations, DOR operations, DOR CLI. |
| **6** | `test_cli_runner.py` | Job runner lifecycle. |
| **7** | `test_processor_abc.py`, `test_processor_ping.py`, `test_processor_cosim.py`, `test_processor_primes.py` | End-to-end processor tests (build image, deploy, submit, verify output). |
| **8** | `test_rti.py`, `test_cli_rti.py` | RTI operations (Docker and optionally AWS). These tests are slow (10+ minutes). |
| **9** | `test_rti_2node.py` | Two-node P2P setup: storage node + execution node. |

### Running by Wave

```bash
# Wave 1 (fast unit tests)
python -m pytest simaas/tests/test_unit_core.py simaas/tests/test_unit_helpers.py simaas/tests/test_errors.py simaas/tests/test_logging.py -v

# Wave 7 (processor tests)
python -m pytest simaas/tests/test_processor_abc.py simaas/tests/test_processor_ping.py simaas/tests/test_processor_cosim.py simaas/tests/test_processor_primes.py -v
```

## Test Markers

| Marker | Description |
|--------|-------------|
| `docker_only` | Requires Docker. Skipped if Docker is not available. |
| `aws_only` | Requires AWS credentials. Skipped if AWS env vars are not set. |
| `slow` | Long-running tests. |
| `serial` | Must not run concurrently with other tests. |
| `integration` | Integration tests that start real services. |
| `e2e` | End-to-end tests covering the full workflow. |

## Docker Image Caching

Processor tests (Wave 7+) build Processor Docker Images. These builds take time but the images are cached by Docker. Important:

**If you modify code that gets baked into PDIs** (e.g., `cmd_job_runner.py`, `namespace/sync.py`, P2P code), the cached images will contain the old code. You must clean the images before re-testing:

```bash
docker images --format "{{.Repository}}:{{.Tag}}" | grep "^proc-" | xargs -r docker rmi -f
```

Alternatively, run Wave 0 (`test_cli_image.py`) to rebuild all images from scratch.

## Async Test Patterns

The codebase is async-native. Tests use `pytest-asyncio` for async test functions. For calling async code from synchronous test contexts, use `run_coro_safely()` from `simaas.core.async_helpers`.

## Test Fixtures

Key fixtures are defined in dedicated fixture files:

| File | Provides |
|------|----------|
| `fixture_core.py` | Keystores, identities, temporary directories |
| `fixture_dor.py` | DOR service instances, sample data objects |
| `fixture_rti.py` | RTI service instances, deployed processors |
| `fixture_rti_2node.py` | Two-node setup (storage + execution) |
| `fixture_mocks.py` | Mock objects for unit tests |
| `conftest.py` | Session-scoped setup, pytest configuration |

## Debugging Tips

**Tests appear stuck?** They may be building Docker images. Check:

```bash
docker ps                      # shows running build containers
docker images | grep proc-     # shows built images with timestamps
```

**Debugging a specific RTI test?** Run it in isolation with the Docker filter:

```bash
python -m pytest simaas/tests/test_rti.py::test_job_submit_and_retrieve -k docker -v
```

**Flaky test?** Check if it depends on port allocation (the Docker RTI allocates ports from the 6000-9000 range) or on timing (batch synchronisation, status polling).
