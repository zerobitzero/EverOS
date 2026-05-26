"""Unit tests for ``agentic_layer.memory_manager.MemoryManager``.

Phase 2 T2.4 of the code-quality roadmap. Targets the thin orchestration
layer that dispatches retrieval and writes — specifically the error
fallback paths that the audit flagged as catch-all anti-patterns
(possibly-unbound, swallow-and-return-empty).

Mock strategy: patch the DI container so __init__ resolves to MagicMock
services. Override individual methods on the instance for path-specific
behavior. No real database, no real LLM.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_specs.dtos.memory import RetrieveMemRequest, RetrieveMemResponse
from api_specs.memory_models import MemoryType, RetrieveMethod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_manager():
    """Construct a MemoryManager with the DI lookups stubbed out.

    Returns a real MemoryManager instance — only the boundaries it owns
    are mocked. Individual tests further override instance methods when
    they want to control the search-side behavior.
    """
    with patch(
        "agentic_layer.memory_manager.get_bean_by_type",
        return_value=MagicMock(),
    ):
        from agentic_layer.memory_manager import MemoryManager

        return MemoryManager()


def _make_request(
    retrieve_method: RetrieveMethod = RetrieveMethod.HYBRID,
    query: str = "what did the user say about coffee",
    top_k: int = 5,
    memory_types=None,
) -> RetrieveMemRequest:
    return RetrieveMemRequest(
        user_id="u_test",
        group_ids=None,
        memory_types=memory_types
        if memory_types is not None
        else [MemoryType.EPISODIC_MEMORY],
        top_k=top_k,
        query=query,
        retrieve_method=retrieve_method,
    )


# ===========================================================================
# retrieve_mem_hybrid
# ===========================================================================


class TestRetrieveMemHybrid:
    """Tests for MemoryManager.retrieve_mem_hybrid.

    The audit flagged this as a swallow-and-return-empty handler. The
    contract is: never raise — degrade to an empty response instead.
    These tests pin that contract so a future refactor that propagates
    errors must be a deliberate, reviewed change.
    """

    @pytest.mark.asyncio
    async def test_happy_path_returns_response(self, memory_manager):
        """Hits flow through to _to_response and are returned."""
        hits = [{"id": "m1", "score": 0.9}, {"id": "m2", "score": 0.7}]
        expected = MagicMock(spec=RetrieveMemResponse)

        memory_manager._search_hybrid = AsyncMock(return_value=hits)
        memory_manager._to_response = AsyncMock(return_value=expected)

        result = await memory_manager.retrieve_mem_hybrid(_make_request())

        assert result is expected
        memory_manager._search_hybrid.assert_awaited_once()
        memory_manager._to_response.assert_awaited_once()
        # _to_response should have been called with the hits and the request.
        called_hits, _called_req = memory_manager._to_response.await_args.args
        assert called_hits == hits

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty_response(self, memory_manager):
        """If the hybrid search raises, we fall back to an empty response.

        Contract: never propagate. This is one of the audit's named
        catch-all sites — we keep the behavior under test until a
        Phase 3 W7 refactor deliberately changes it.
        """
        empty = MagicMock(spec=RetrieveMemResponse)
        memory_manager._search_hybrid = AsyncMock(side_effect=ConnectionError("ES down"))
        memory_manager._to_response = AsyncMock(return_value=empty)

        result = await memory_manager.retrieve_mem_hybrid(_make_request())

        assert result is empty
        # _to_response is still called, but with an empty hits list.
        called_hits, _called_req = memory_manager._to_response.await_args.args
        assert called_hits == []

    @pytest.mark.asyncio
    async def test_timeout_also_swallowed(self, memory_manager):
        """Timeouts use the same fallback path — verifies the except is broad."""
        empty = MagicMock(spec=RetrieveMemResponse)
        memory_manager._search_hybrid = AsyncMock(side_effect=asyncio.TimeoutError())
        memory_manager._to_response = AsyncMock(return_value=empty)

        result = await memory_manager.retrieve_mem_hybrid(_make_request())

        assert result is empty

    @pytest.mark.asyncio
    async def test_empty_memory_types_uses_unknown_label(self, memory_manager):
        """When memory_types is empty, no crash — request still flows."""
        memory_manager._search_hybrid = AsyncMock(return_value=[])
        memory_manager._to_response = AsyncMock(
            return_value=MagicMock(spec=RetrieveMemResponse)
        )

        req = _make_request(memory_types=[])
        # memory_types=[] would fail Pydantic validation? It's
        # default_factory=list, so empty is OK as a default — but the
        # request validator requires user_id or group_ids, which we
        # provide. memory_type label code path uses 'unknown'.
        await memory_manager.retrieve_mem_hybrid(req)

        memory_manager._search_hybrid.assert_awaited_once()


# ===========================================================================
# _classify_retrieve_error
# ===========================================================================


class TestClassifyRetrieveError:
    """Tests for MemoryManager._classify_retrieve_error.

    Verifies the metric label produced for the most common failure
    modes. Tied to the error_type label of `retrieve_errors_total`
    (see slo_definitions.md and exception_handling_analysis.md).
    """

    def test_timeout_class_name(self, memory_manager):
        label = memory_manager._classify_retrieve_error(asyncio.TimeoutError())
        assert label == "timeout"

    def test_connection_error(self, memory_manager):
        label = memory_manager._classify_retrieve_error(
            ConnectionError("backend unreachable")
        )
        assert label == "connection_error"

    def test_generic_exception_uses_snake_case_class_name(self, memory_manager):
        class WeirdError(Exception):
            pass

        label = memory_manager._classify_retrieve_error(WeirdError("boom"))
        # classify_exception falls through to snake-cased class name when
        # no semantic category matches.
        assert label == "weird_error"

    def test_validation_message_matches_category(self, memory_manager):
        label = memory_manager._classify_retrieve_error(
            ValueError("invalid query: empty")
        )
        # "invalid" in message → validation_error category
        assert label == "validation_error"


# ===========================================================================
# retrieve_mem dispatcher
# ===========================================================================


class TestRetrieveMemDispatcher:
    """Tests for MemoryManager.retrieve_mem.

    This is the public entry point that fans out into keyword / vector /
    hybrid / agentic search. The audit noted it also has a catch-all that
    returns an empty response — the dispatcher must not raise.
    """

    @pytest.mark.asyncio
    async def test_dispatches_to_hybrid(self, memory_manager):
        expected = MagicMock(spec=RetrieveMemResponse)
        memory_manager.retrieve_mem_hybrid = AsyncMock(return_value=expected)
        memory_manager._get_pending_messages = AsyncMock(return_value=[])
        memory_manager._build_combined_response = MagicMock(return_value=expected)

        await memory_manager.retrieve_mem(_make_request(RetrieveMethod.HYBRID))

        memory_manager.retrieve_mem_hybrid.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_to_keyword(self, memory_manager):
        memory_manager.retrieve_mem_keyword = AsyncMock(
            return_value=MagicMock(spec=RetrieveMemResponse)
        )
        memory_manager._get_pending_messages = AsyncMock(return_value=[])
        memory_manager._build_combined_response = MagicMock(
            return_value=MagicMock(spec=RetrieveMemResponse)
        )

        await memory_manager.retrieve_mem(_make_request(RetrieveMethod.KEYWORD))

        memory_manager.retrieve_mem_keyword.assert_awaited_once()

    @pytest.mark.xfail(
        reason=(
            "Audit-flagged bug: when retrieve_mem_request is None, the "
            "ValueError is caught but the fallback path then calls "
            "QueryMetadata.from_request(None) which raises AttributeError. "
            "Tracked in exception_handling_analysis.md; will be fixed in "
            "Phase 3 W7. Once fixed, this test flips to xpass."
        ),
        strict=True,
    )
    @pytest.mark.asyncio
    async def test_no_request_returns_empty_response(self, memory_manager):
        """Passing a falsy request should yield an empty response, not crash."""
        result = await memory_manager.retrieve_mem(None)

        assert isinstance(result, RetrieveMemResponse)
        assert result.memories == []
        assert result.total_count == 0

    @pytest.mark.asyncio
    async def test_downstream_failure_does_not_propagate(self, memory_manager):
        """If the chosen retrieval method raises, we return an empty response.

        Contract pin — same audit category as retrieve_mem_hybrid.
        """
        memory_manager.retrieve_mem_hybrid = AsyncMock(
            side_effect=RuntimeError("rerank crashed")
        )
        memory_manager._get_pending_messages = AsyncMock(return_value=[])

        result = await memory_manager.retrieve_mem(_make_request(RetrieveMethod.HYBRID))

        assert isinstance(result, RetrieveMemResponse)
        assert result.memories == []


# ===========================================================================
# memorize delegation
# ===========================================================================


class TestMemorize:
    """Tests for MemoryManager.memorize.

    This is a thin pass-through to biz_layer.mem_memorize.memorize. The
    test pins the contract that exceptions are NOT swallowed here — that
    happens deeper in biz_layer. Refactors should be deliberate.
    """

    @pytest.mark.asyncio
    async def test_delegates_to_biz_layer(self, memory_manager):
        with patch(
            "agentic_layer.memory_manager.memorize",
            new=AsyncMock(return_value=7),
        ) as mock_memorize:
            req = MagicMock()
            count = await memory_manager.memorize(req)

            assert count == 7
            mock_memorize.assert_awaited_once_with(req)

    @pytest.mark.asyncio
    async def test_propagates_biz_layer_exception(self, memory_manager):
        """No catch here — biz_layer owns the catch-all."""
        with patch(
            "agentic_layer.memory_manager.memorize",
            new=AsyncMock(side_effect=ValueError("bad request")),
        ):
            with pytest.raises(ValueError, match="bad request"):
                await memory_manager.memorize(MagicMock())
