"""Business-layer exception hierarchy.

See ``core.errors`` package docstring for context. This module defines
the concrete classes; ``core/errors/__init__.py`` re-exports them.

Why a custom hierarchy at all?

1. **Pattern match at the boundary, not deep inside.** Today the
   business layer catches ``Exception``, logs a string, and returns an
   empty result. The controller can't tell apart a transient backend
   blip from a malformed request. With typed errors the controller can
   choose: 503 for ``SearchBackendUnavailable``, 400 for
   ``LLMOutputFormatError`` after retries exhaust, etc.
2. **Carry context across the catch boundary.** Plain ``Exception``
   loses tenant_id, stage, and request id once the message is
   formatted. Subclasses preserve those fields as attributes so
   structured logging and Sentry grouping work without parsing
   message strings.
3. **Stable taxonomy for metrics.** The existing
   ``record_*_error(error_type=...)`` helpers use exception class names
   already (T1.3). Naming the buckets explicitly here means a refactor
   of the underlying call chain doesn't accidentally rename the metric
   label.
"""

from __future__ import annotations

from typing import Any, Optional


class EverCoreError(Exception):
    """Common root for every typed EverCore failure.

    Controllers may install a single ``except EverCoreError`` as the
    domain-level fallback. Subclasses provide finer-grained matching
    higher in the chain when needed.

    Attributes:
        tenant_id: Tenant under which the failure occurred. May be
            ``None`` if the failure pre-dates tenant resolution (e.g.
            during auth).
        cause: Optional underlying exception preserved for logging.
            Use ``raise X from y`` whenever possible; this attribute
            exists for callers that catch then re-raise across an
            async boundary where the ``__cause__`` chain would
            otherwise be lost.
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: Optional[str] = None,
        cause: Optional[BaseException] = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.tenant_id = tenant_id
        self.cause = cause
        self.context = context

    def __repr__(self) -> str:
        bits = [f"message={self.args[0]!r}"]
        if self.tenant_id is not None:
            bits.append(f"tenant_id={self.tenant_id!r}")
        if self.context:
            bits.append(f"context={self.context!r}")
        return f"{type(self).__name__}({', '.join(bits)})"


# ---------------------------------------------------------------------------
# Write path — memorize
# ---------------------------------------------------------------------------


class MemorizeError(EverCoreError):
    """Anything that prevents a memorize request from completing.

    Subclasses narrow the cause. Catching ``MemorizeError`` at the
    controller boundary is the default; catch a subclass when the
    response differs (e.g. retryable vs not).
    """


class MemoryCellPersistFailed(MemorizeError):
    """A MemCell could not be saved to its target store.

    Distinguished from extraction failure: extraction succeeded, the
    write side did not. Typically retryable.
    """


class ExtractionError(EverCoreError):
    """LLM-driven extraction failed.

    Covers boundary detection, episode/atomic-fact/foresight
    extraction, agent case/skill extraction. The retry policy for the
    extraction layer (Phase 3 W6 Type-B "feedback retry") should
    typically convert provider-side failures into this class before
    propagating.
    """


class LLMOutputFormatError(ExtractionError):
    """LLM responded but the output didn't match the expected schema.

    Distinguished from a provider-side failure: the call succeeded,
    the *content* was wrong. The Type-B "feedback retry" pattern
    catches this specifically so it can include the parsing error in
    the next prompt; other failures don't get that treatment.
    """


# ---------------------------------------------------------------------------
# Read path — retrieve
# ---------------------------------------------------------------------------


class RetrieveError(EverCoreError):
    """Anything that prevents a retrieve request from completing.

    Default controller behavior should be to return 503 with retry
    guidance for ``SearchBackendUnavailable``, and 500 (logged loudly)
    for any other ``RetrieveError`` since those indicate a logic bug
    rather than a transient backend issue.
    """


class SearchBackendUnavailable(RetrieveError):
    """Elasticsearch / Milvus / Redis is unreachable or returning errors.

    Retryable in principle. Today's catch-all returns an empty result
    set; the Phase 3 W7 migration replaces that with this error so the
    controller can pick the response code.
    """


class ProviderUnavailable(EverCoreError):
    """An external LLM / embedding provider is unreachable or rate-limited.

    Carries a ``provider`` context field so logging / metrics can
    attribute the failure correctly. Sits at the root (not under
    ``RetrieveError`` or ``ExtractionError``) because both write and
    read paths can hit the same condition.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        tenant_id: Optional[str] = None,
        cause: Optional[BaseException] = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message, tenant_id=tenant_id, cause=cause, provider=provider, **context
        )
        self.provider = provider
