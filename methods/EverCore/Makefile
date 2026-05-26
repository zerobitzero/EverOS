.PHONY: dev-setup setup-hooks lint ruff ruff-fix typecheck typecheck-pyright test test-unit test-integration test-e2e test-cov benchmark clean help

# Default target
help:
	@echo "Available targets:"
	@echo "  dev-setup          - Full dev environment setup (sync deps + install hooks)"
	@echo "  setup-hooks        - Install pre-commit hooks only"
	@echo "  lint               - Run linters (black + i18n)"
	@echo "  ruff               - Run ruff check + format check (read-only)"
	@echo "  ruff-fix           - Run ruff with --fix and apply formatter"
	@echo "  typecheck          - Run ty type checker (default, fast)"
	@echo "  typecheck-pyright  - Run pyright type checker (fallback)"
	@echo "  test               - Run all tests"
	@echo "  test-unit          - Run unit tests only (no docker required)"
	@echo "  test-integration   - Run integration tests (needs docker-compose up)"
	@echo "  test-e2e           - Run end-to-end tests"
	@echo "  test-cov           - Run unit tests with coverage report (xml + term)"
	@echo "  benchmark          - Run perf benchmarks (needs bench group + docker)"
	@echo "  clean              - Clean up generated files"
	@echo "  help               - Show this help message"

# Full development environment setup
dev-setup:
	@echo "Setting up development environment..."
	@echo "1. Syncing dependencies..."
	uv sync --dev
	@echo "2. Installing pre-commit hooks..."
	@$(MAKE) setup-hooks
	@echo "Development environment is ready!"

# Install pre-commit hooks (clean install)
setup-hooks:
	@echo "Removing existing hooks..."
	rm -f .git/hooks/pre-commit .git/hooks/commit-msg
	@echo "Installing pre-commit hooks..."
	pre-commit install --hook-type pre-commit --hook-type commit-msg
	@echo "Done! Pre-commit hooks installed."

# Run linters
lint:
	@echo "Running black..."
	black src/
	@echo "Running i18n check..."
	PYTHONPATH=src python -m devops_scripts.i18n.i18n_tool check

# Run ruff (lint + format check). Read-only; CI-friendly.
ruff:
	@echo "Running ruff check..."
	uv run ruff check src/
	@echo "Running ruff format --check..."
	uv run ruff format --check src/

# Apply ruff auto-fixes and formatting.
ruff-fix:
	@echo "Running ruff check --fix..."
	uv run ruff check --fix src/
	@echo "Running ruff format..."
	uv run ruff format src/

# Run ty type checker (default).
# Configured in pyproject.toml under [tool.ty]. ~10x faster than pyright.
# Note: ty is pre-1.0 — pin a floor in pyproject.toml and accept that diagnostic
# wording may shift between minor releases.
typecheck:
	@echo "Running ty..."
	uv run ty check --ignore all --error possibly-unresolved-reference

# Run pyright type checker (fallback).
# pyrightconfig.json controls scope and strictness; current mode is "off"
# (syntax/import resolution only). Tighten there to surface more checks.
typecheck-pyright:
	@echo "Running pyright..."
	uv run pyright

# Run tests
test:
	PYTHONPATH=src uv run pytest tests/

# Unit tests only. No docker-compose dependencies; safe for CI on every PR.
# Markers are applied automatically in tests/conftest.py based on file path.
test-unit:
	PYTHONPATH=src uv run pytest tests/ -m unit

# Integration tests. Require docker-compose services (Redis, MongoDB, ES, Milvus).
test-integration:
	PYTHONPATH=src uv run pytest tests/ -m integration

# End-to-end tests. Heaviest tier; run on demand or in a nightly job.
test-e2e:
	PYTHONPATH=src uv run pytest tests/ -m e2e

# Coverage report (XML for CI tooling + terminal summary).
test-cov:
	PYTHONPATH=src uv run pytest tests/ -m unit \
		--cov=src --cov-report=xml --cov-report=term

# Performance benchmarks. Requires the optional `bench` group
# (`uv sync --group bench`) and RUN_BENCHMARKS=1 plus a running
# docker-compose stack. See tests/benchmarks/README.md.
benchmark:
	PYTHONPATH=src RUN_BENCHMARKS=1 uv run pytest tests/benchmarks/ \
		--benchmark-only --benchmark-columns=min,mean,median,p95,max

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf .pytest_cache 2>/dev/null || true
	@echo "Cleaned up generated files."
