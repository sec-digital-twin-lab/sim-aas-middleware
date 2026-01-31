# Testing Guide

## Environment Setup

### Virtual Environment

Tests must be run using the project's virtual environment:

```bash
# Create virtual environment (if not exists)
python3.13 -m venv .venv

# Install dependencies
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

### Environment Variables

Create a `.env` file in the project root. The test framework automatically loads it via `python-dotenv`.

#### Required for Docker RTI Tests

| Variable | Description |
|----------|-------------|
| `SIMAAS_REPO_PATH` | Absolute path to the sim-aas-middleware repository |

#### Required for AWS RTI Tests

See [Running a Sim-aaS Node - AWS RTI Service](usage_run_simaas_node.md#aws-rti-service) for detailed AWS setup.

| Variable | Description |
|----------|-------------|
| `SIMAAS_AWS_REGION` | AWS region (e.g., `ap-southeast-1`) |
| `SIMAAS_AWS_ACCESS_KEY_ID` | AWS access key ID |
| `SIMAAS_AWS_SECRET_ACCESS_KEY` | AWS secret access key |
| `SIMAAS_AWS_ROLE_ARN` | IAM role ARN for Batch execution |
| `SIMAAS_AWS_JOB_QUEUE` | AWS Batch job queue name |

#### AWS SSH Tunneling (for local development)

| Variable | Description |
|----------|-------------|
| `SSH_TUNNEL_HOST` | EC2 instance public DNS |
| `SSH_TUNNEL_USER` | SSH username (typically `ubuntu`) |
| `SSH_TUNNEL_KEY_PATH` | Path to SSH private key |
| `SIMAAS_CUSTODIAN_HOST` | EC2 instance private DNS |

#### GitHub Credentials (for image builds)

| Variable | Description |
|----------|-------------|
| `GITHUB_USERNAME` | GitHub username |
| `GITHUB_TOKEN` | GitHub Personal Access Token |

### Example `.env` File

```bash
# Required for Docker RTI tests
SIMAAS_REPO_PATH=/path/to/sim-aas-middleware

# GitHub credentials (optional)
GITHUB_USERNAME=your-username
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# AWS RTI (optional)
SIMAAS_AWS_REGION=ap-southeast-1
SIMAAS_AWS_ACCESS_KEY_ID=AKIA...
SIMAAS_AWS_SECRET_ACCESS_KEY=...
SIMAAS_AWS_ROLE_ARN=arn:aws:iam::123456789:role/simaas-role
SIMAAS_AWS_JOB_QUEUE=simaas-queue
```

## Running Tests

### Prerequisites

- Docker must be running (required for RTI tests)
- Virtual environment activated or use `.venv/bin/python` prefix

### Commands

```bash
# Run all tests
.venv/bin/python -m pytest simaas/tests/ -v

# Run specific category
.venv/bin/python -m pytest simaas/tests/test_unit_*.py -v    # Unit tests
.venv/bin/python -m pytest simaas/tests/test_cli_*.py -v     # CLI tests
.venv/bin/python -m pytest simaas/tests/test_processor_*.py -v  # Processor tests

# Run with coverage
.venv/bin/python -m coverage run -m pytest simaas/tests/ -v
.venv/bin/python -m coverage report --ignore-errors --include="simaas/*" --omit="simaas/tests/*"

# Run specific test file
.venv/bin/python -m pytest simaas/tests/test_dor.py -v

# Run specific test function
.venv/bin/python -m pytest simaas/tests/test_dor.py::test_dor_add_search -v

# Run by marker
.venv/bin/python -m pytest simaas/tests/ -v -m "docker_only"
.venv/bin/python -m pytest simaas/tests/ -v -m "aws_only"
```

## Async Tests

Some tests use `pytest-asyncio`:

```bash
# Run async tests
.venv/bin/python -m pytest simaas/tests/test_p2p.py -v
```

**Patterns**:
- Use `@pytest.mark.asyncio` decorator for async test functions
- Use `run_coro_safely()` from `simaas.core.async_helpers` when calling coroutines from sync context

## Test Categories

| Category | Files | Description |
|----------|-------|-------------|
| Unit | `test_unit_core.py`, `test_unit_helpers.py` | Core functionality, test helpers |
| CLI | `test_cli_*.py` (6 files) | Command-line interface |
| Services | `test_dor.py`, `test_namespace.py`, `test_nodedb.py`, `test_p2p.py`, `test_rest.py`, `test_rti.py` | Service integration |
| Processors | `test_processor_*.py` (4 files) | Processor examples |

## Test Statistics

For current test timings, coverage details, and recommended timeouts, see `TEST_STATS.md` in the repository root.

**Summary**:
- **Total tests**: 160
- **Total runtime**: ~19 minutes
- **Coverage**: 82% (target: 80%)

## Notes

- **Docker Requirement**: RTI-related tests require Docker to be running
- **AWS Tests**: Run with mock/fallback behavior if AWS credentials are not configured
- **GitHub Credentials**: Required for tests that build processor images from GitHub
- **IDE Support**: Tests work in PyCharm and other IDEs with pytest support
