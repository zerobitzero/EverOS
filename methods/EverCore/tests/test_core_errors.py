"""Unit tests for the ``core.errors`` exception hierarchy.

Phase 3 W7 (subset). Pins the public contract of the hierarchy so
later migration PRs can rely on it without rediscovering the shape.
"""

from __future__ import annotations

import pytest

from core.errors import (
    EverCoreError,
    ExtractionError,
    LLMOutputFormatError,
    MemorizeError,
    MemoryCellPersistFailed,
    ProviderUnavailable,
    RetrieveError,
    SearchBackendUnavailable,
)


class TestHierarchy:
    """Every concrete class roots at EverCoreError so the controller-side
    catch-all stays a single line."""

    def test_every_error_subclasses_evercoreerror(self):
        for cls in (
            MemorizeError,
            MemoryCellPersistFailed,
            ExtractionError,
            LLMOutputFormatError,
            RetrieveError,
            SearchBackendUnavailable,
            ProviderUnavailable,
        ):
            assert issubclass(cls, EverCoreError), cls.__name__

    def test_memorize_subclass_layout(self):
        assert issubclass(MemoryCellPersistFailed, MemorizeError)
        assert not issubclass(MemorizeError, MemoryCellPersistFailed)

    def test_extraction_subclass_layout(self):
        assert issubclass(LLMOutputFormatError, ExtractionError)

    def test_retrieve_subclass_layout(self):
        assert issubclass(SearchBackendUnavailable, RetrieveError)

    def test_provider_unavailable_is_root_level(self):
        """Sits directly under EverCoreError — not Memorize or Retrieve —
        because both paths can hit a provider outage."""
        assert issubclass(ProviderUnavailable, EverCoreError)
        assert not issubclass(ProviderUnavailable, RetrieveError)
        assert not issubclass(ProviderUnavailable, MemorizeError)


class TestEverCoreErrorAttributes:
    """The base preserves tenant_id, cause, and arbitrary context."""

    def test_minimal_construction(self):
        err = EverCoreError("boom")
        assert str(err) == "boom"
        assert err.tenant_id is None
        assert err.cause is None
        assert err.context == {}

    def test_tenant_id_preserved(self):
        err = EverCoreError("boom", tenant_id="t_1")
        assert err.tenant_id == "t_1"

    def test_cause_preserved_for_async_re_raise(self):
        underlying = ValueError("orig")
        err = EverCoreError("wrapped", cause=underlying)
        assert err.cause is underlying

    def test_context_captures_arbitrary_kwargs(self):
        err = EverCoreError("boom", stage="vectorize", attempt=3)
        assert err.context == {"stage": "vectorize", "attempt": 3}

    def test_repr_includes_tenant_and_context(self):
        err = EverCoreError("boom", tenant_id="t_1", stage="rerank")
        rep = repr(err)
        assert "tenant_id='t_1'" in rep
        assert "stage" in rep


class TestProviderUnavailable:
    """ProviderUnavailable adds a typed ``provider`` field."""

    def test_provider_field(self):
        err = ProviderUnavailable("429 from openai", provider="openai")
        assert err.provider == "openai"
        assert err.context["provider"] == "openai"

    def test_provider_optional(self):
        err = ProviderUnavailable("upstream down")
        assert err.provider is None

    def test_tenant_and_provider_together(self):
        err = ProviderUnavailable(
            "rate limited", provider="anthropic", tenant_id="t_42"
        )
        assert err.provider == "anthropic"
        assert err.tenant_id == "t_42"


class TestRaiseFromUsage:
    """The hierarchy is meant to be used with ``raise X from y``.

    The chained exception remains accessible via ``__cause__`` so
    logging and stack capture work the standard way.
    """

    def test_raise_from_preserves_chain(self):
        try:
            try:
                raise ConnectionError("backend down")
            except ConnectionError as exc:
                raise SearchBackendUnavailable(
                    "elasticsearch unreachable", cause=exc
                ) from exc
        except SearchBackendUnavailable as caught:
            assert isinstance(caught.__cause__, ConnectionError)
            assert caught.cause is caught.__cause__

    def test_pattern_match_at_root(self):
        """Controllers can catch the root and still receive subclasses."""
        with pytest.raises(EverCoreError):
            raise MemoryCellPersistFailed("mongo timeout")
