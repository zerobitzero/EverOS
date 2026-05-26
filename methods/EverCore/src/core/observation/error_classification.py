"""Shared exception-to-label classifier for Prometheus metrics.

Why this module exists
----------------------
Before this helper landed, every service had its own ``_classify_error``
method using string-matching against ``str(error)``, and the default
branch returned the hardcoded literal ``"unknown"``. That blew up
observability in two ways:

* Multiple sites returned different strings for the same exception class
  (``"connection_error"`` vs ``"api_error"``), so cross-service alerting
  was impossible.
* The default ``"unknown"`` bucket swallowed every unfamiliar exception,
  so Grafana's error-type breakdown was useless once anything new
  appeared.

The function here is the single source of truth. Specific semantic
buckets (``timeout``, ``rate_limit``, ``validation_error``,
``connection_error``) are still returned when recognised, and *anything
else* falls through to the exception class name converted to
``snake_case`` so the label is always a concrete, queryable signal.
"""

from __future__ import annotations

import re


_CAMEL_TO_SNAKE_FIRST = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_TO_SNAKE_REST = re.compile(r"([a-z0-9])([A-Z])")


def _to_snake_case(name: str) -> str:
    """``HTTPSConnectionError`` → ``https_connection_error``."""
    s1 = _CAMEL_TO_SNAKE_FIRST.sub(r"\1_\2", name)
    return _CAMEL_TO_SNAKE_REST.sub(r"\1_\2", s1).lower()


def classify_exception(error: BaseException) -> str:
    """Return a stable, Prometheus-friendly label for ``error``.

    Lookup order:

    1. Known semantic categories matched against the exception class name
       and message — keeps existing dashboards working.
    2. The exception class name converted to ``snake_case`` for anything
       else. This guarantees the label is never the opaque ``"unknown"``.
    """
    cls_name = type(error).__name__
    cls_lower = cls_name.lower()
    msg_lower = str(error).lower()

    # Semantic categories first so existing dashboards keep grouping
    # well-known failure modes.
    if "timeout" in cls_lower or "timeout" in msg_lower or "timed out" in msg_lower:
        return "timeout"
    if ("rate" in msg_lower and "limit" in msg_lower) or "ratelimit" in cls_lower:
        return "rate_limit"
    if (
        "validation" in cls_lower
        or "validation" in msg_lower
        or "invalid" in msg_lower
    ):
        return "validation_error"
    if (
        "connection" in cls_lower
        or "connect" in cls_lower
        or "connection" in msg_lower
    ):
        return "connection_error"
    if "notfound" in cls_lower or "not found" in msg_lower:
        return "not_found"

    # Fall through: surface the concrete exception class.
    return _to_snake_case(cls_name)
