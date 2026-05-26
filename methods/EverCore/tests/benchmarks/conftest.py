"""Pytest configuration for the benchmark suite.

Benchmarks live in this directory only. Auto-applies the `benchmark`
marker and skips collection entirely when ``pytest-benchmark`` is not
installed, so a developer with just the default dev group can still
collect the rest of the test tree without errors.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    """Tag every collected item in this directory with `benchmark`."""
    for item in items:
        if "tests/benchmarks/" in str(item.fspath):
            item.add_marker(pytest.mark.benchmark)


def pytest_configure(config):
    """Warn loudly if pytest-benchmark is missing.

    We don't fail the whole session — other tests may still run — but
    a missing plugin would cause the actual benchmarks to silently
    behave as plain pytest functions.
    """
    if config.getoption("--collect-only"):
        return
    try:
        import pytest_benchmark  # noqa: F401
    except ImportError:
        config.issue_config_time_warning(
            pytest.PytestConfigWarning(
                "pytest-benchmark not installed; benchmark timings will not be "
                "captured. Install with `uv sync --group bench`."
            ),
            stacklevel=2,
        )
