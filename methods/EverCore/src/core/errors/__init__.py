"""EverCore custom exception hierarchy.

Phase 3 W7 of the code-quality roadmap. The audit
([`docs/dev_docs/exception_handling_analysis.md`](../../../docs/dev_docs/exception_handling_analysis.md))
identified ~71% of try blocks as bare ``except Exception``. The cleanup
plan is to replace business-layer catch-alls with typed exceptions
propagated upward, then catch the specific types at controller
boundaries.

This module is the foundation. It is **purely additive** — no existing
call sites are migrated here. Migration happens in follow-up PRs that
each carry their own test coverage and staging soak.

Naming convention:
- ``EverCoreError`` — the common root. Every typed exception in this
  hierarchy derives from it so controllers can install a single
  fallback ``except EverCoreError`` while still pattern-matching
  specific subtypes higher in the chain.
- ``*Error`` — stage / domain bucket (``MemorizeError``,
  ``RetrieveError``, ``ExtractionError``).
- ``*Failed`` — a specific failure mode within a bucket
  (``MemoryCellPersistFailed``, ``ProviderUnavailable``).

Each error carries a ``tenant_id`` so structured logging can attach the
right scope without an additional logger.bind call. Tenant-scoping is
optional — pass ``None`` when the failure happens before tenant context
is available.
"""

from core.errors.business import (
    EverCoreError,
    ExtractionError,
    LLMOutputFormatError,
    MemorizeError,
    MemoryCellPersistFailed,
    ProviderUnavailable,
    RetrieveError,
    SearchBackendUnavailable,
)


__all__ = [
    "EverCoreError",
    "ExtractionError",
    "LLMOutputFormatError",
    "MemorizeError",
    "MemoryCellPersistFailed",
    "ProviderUnavailable",
    "RetrieveError",
    "SearchBackendUnavailable",
]
