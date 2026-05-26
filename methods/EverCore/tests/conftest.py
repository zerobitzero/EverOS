"""Pytest configuration shared across the EverCore test suite.

Auto-marks tests by path so the existing 67 files do not need to be edited
one by one. The marker convention matches the Phase 2 roadmap (T2.3):

- ``tests/integration/**`` → ``integration``
- ``tests/*_e2e.py``       → ``e2e``
- everything else          → ``unit``

Tests can still apply explicit markers; auto-application is skipped when
any of unit/integration/e2e is already present on the item.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_EXPLICIT = {"unit", "integration", "e2e"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    rootpath = Path(config.rootpath).resolve()
    for item in items:
        if any(m.name in _EXPLICIT for m in item.iter_markers()):
            continue
        path = Path(item.fspath).resolve()
        try:
            rel = path.relative_to(rootpath)
        except ValueError:
            rel = path
        parts = rel.parts
        name = path.name
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif name.endswith("_e2e.py"):
            item.add_marker(pytest.mark.e2e)
        else:
            item.add_marker(pytest.mark.unit)
