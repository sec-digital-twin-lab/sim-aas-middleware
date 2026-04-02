# Contributing

Thank you for your interest in contributing to this project.

## Before You Start

This project has not yet had external contributors. If you're interested in contributing, please **open an issue first** to discuss your proposed changes before starting any work.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/sec-digital-twin-lab/sim-aas-middleware.git
cd sim-aas-middleware

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install ".[dev]"
```

## Branch Naming

- **Development branches**: `dev-x.y.z` - collects features and fixes for a version
- **Feature/fix branches**: `<issue#>-short-description` (e.g., `42-add-retry-logic`)

## Pull Request Process

1. Create a branch from the current development branch (`dev-x.y.z`)
2. Make your changes
3. Ensure tests pass: `pytest ./simaas/tests`
4. Ensure linting passes: `ruff check .`
5. Open a PR with `Closes #<issue>` in the description to link and auto-close the issue

## Code Style

This project uses [Ruff](https://github.com/astral-sh/ruff) for linting. Run `ruff check .` before submitting.
