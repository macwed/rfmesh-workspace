# rfmesh — task recipes for `just`.
#
# These are the verification commands AGENTS.md §5 names. A workstream
# agent runs `just verify` before declaring a ticket done and pastes the
# output into the conversation. A ticket-scoped agent runs
# `just ticket-verify <pkg>` to check only its own package.
#
# Install just: https://github.com/casey/just
#   macOS:  brew install just
#   Linux:  cargo install just  (or distro package)

# Default target — what `just` alone does
default: verify

# The full verify protocol — lead's quality gate.
verify: lint type test
    @echo
    @echo "✓ verify complete"

# Linting and formatting
lint:
    uv run ruff check .
    uv run ruff format --check .

# Format in place (use before committing if `just lint` fails on formatting)
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Type checking — strict, workspace-wide
type:
    uv run mypy packages/

# Tests — non-hardware by default
test:
    uv run pytest -m "not hardware"

# Tests with coverage report
test-cov:
    uv run pytest -m "not hardware" --cov --cov-report=term-missing

# Hardware tests — only when SDR / servo is plugged in. Will fail in CI.
test-hardware:
    uv run pytest -m hardware

# Ticket-scoped verification — what a Claude Code agent runs before
# declaring done. {{package}} is the package the ticket touches.
# Example: just ticket-verify rfmesh-dsp
ticket-verify package:
    uv run pytest packages/{{package}}
    uv run mypy packages/{{package}}
    uv run ruff check packages/{{package}}

# Clean build artifacts (does not touch source or git state)
clean:
    rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build
    find . -type d -name __pycache__ -not -path './.git/*' -exec rm -rf {} +
    find . -type d -name '*.egg-info' -not -path './.git/*' -exec rm -rf {} +
