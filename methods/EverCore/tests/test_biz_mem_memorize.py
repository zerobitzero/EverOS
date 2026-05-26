"""Unit tests for ``biz_layer.mem_memorize`` helpers.

Phase 2 T2.4 of the code-quality roadmap. The full ``memorize`` entry
point and ``process_memory_extraction`` orchestration require live
DB/LLM mocks that belong in integration suites. This file targets the
pure-logic helpers and the catch-all-returning-0 sites the audit
flagged, so the contracts are pinned before the Phase 3 W7 refactor.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_specs.memory_types import AgentCase, EpisodeMemory, MemCell, RawDataType
from api_specs.memory_models import MemoryType
from biz_layer.mem_memorize import (
    _clone_episodes_for_users,
    _is_agent_case_quality_sufficient,
    _save_agent_case,
    _should_skip_atomic_fact_for_agent,
    if_memorize,
)
from biz_layer.memorize_config import MemorizeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_memcell(
    *,
    user_id_list=None,
    original_data=None,
    timestamp=None,
    rd_type: RawDataType = RawDataType.CONVERSATION,
) -> MemCell:
    return MemCell(
        user_id_list=user_id_list or ["u1"],
        original_data=original_data
        or [{"message": {"role": "user", "content": "hello"}}],
        timestamp=timestamp or datetime(2026, 1, 1, 0, 0, 0),
        event_id="evt_1",
        group_id="g1",
        type=rd_type,
    )


def _make_agent_case(quality_score=0.8) -> AgentCase:
    return AgentCase(
        memory_type=MemoryType.AGENT_CASE,
        user_id="u1",
        timestamp=datetime(2026, 1, 1),
        task_intent="ship a feature",
        approach="1. plan\n2. write tests\n3. ship",
        quality_score=quality_score,
        vector=[0.1, 0.2, 0.3],
        vector_model="text-embedding-3-small",
    )


# ===========================================================================
# if_memorize
# ===========================================================================


def test_if_memorize_always_true():
    """Currently a placeholder; pin so a future gate change is deliberate."""
    assert if_memorize(_make_memcell()) is True


# ===========================================================================
# _is_agent_case_quality_sufficient
# ===========================================================================


class TestAgentCaseQualityThreshold:
    """Quality-score gating for skill extraction."""

    def test_score_above_threshold(self):
        config = MemorizeConfig(skill_min_quality_score=0.5)
        assert _is_agent_case_quality_sufficient(_make_agent_case(0.8), config) is True

    def test_score_at_threshold(self):
        config = MemorizeConfig(skill_min_quality_score=0.5)
        # `score < threshold` is the rejection condition; equality passes.
        assert _is_agent_case_quality_sufficient(_make_agent_case(0.5), config) is True

    def test_score_below_threshold(self):
        config = MemorizeConfig(skill_min_quality_score=0.5)
        assert _is_agent_case_quality_sufficient(_make_agent_case(0.4), config) is False

    def test_score_none_treated_as_insufficient(self):
        """`None` score is the no-LLM-judgment case — must be skipped."""
        config = MemorizeConfig(skill_min_quality_score=0.2)
        assert _is_agent_case_quality_sufficient(_make_agent_case(None), config) is False


# ===========================================================================
# _should_skip_atomic_fact_for_agent
# ===========================================================================


class TestShouldSkipAtomicFactForAgent:
    """Atomic-fact skip rule for agent conversations.

    Skip when: at least one message has tool_calls or role='tool', AND
    cumulative assistant non-tool-call text length >= 1000 characters.
    """

    def test_no_tool_calls_does_not_skip(self):
        mc = _make_memcell(
            original_data=[
                {"message": {"role": "user", "content": "hello"}},
                {"message": {"role": "assistant", "content": "a" * 5000}},
            ],
            rd_type=RawDataType.AGENTCONVERSATION,
        )
        assert _should_skip_atomic_fact_for_agent(mc) is False

    def test_tool_calls_with_short_assistant_does_not_skip(self):
        mc = _make_memcell(
            original_data=[
                {"message": {"role": "user", "content": "hi"}},
                {
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                        "tool_calls": [{"name": "do"}],
                    }
                },
                {"message": {"role": "assistant", "content": "done"}},
            ],
            rd_type=RawDataType.AGENTCONVERSATION,
        )
        # The tool-call-bearing assistant message is excluded from the
        # length sum; only the bare assistant "done" counts (4 chars).
        assert _should_skip_atomic_fact_for_agent(mc) is False

    def test_tool_calls_with_long_assistant_skips(self):
        mc = _make_memcell(
            original_data=[
                {"message": {"role": "user", "content": "hi"}},
                {
                    "message": {
                        "role": "assistant",
                        "content": "thinking",
                        "tool_calls": [{"name": "search"}],
                    }
                },
                {"message": {"role": "tool", "content": "result"}},
                {"message": {"role": "assistant", "content": "x" * 1200}},
            ],
            rd_type=RawDataType.AGENTCONVERSATION,
        )
        assert _should_skip_atomic_fact_for_agent(mc) is True

    def test_tool_role_alone_counts_as_tool_calls(self):
        """A bare `role=tool` message triggers the gating, even without
        a `tool_calls` field on the assistant turn."""
        mc = _make_memcell(
            original_data=[
                {"message": {"role": "tool", "content": "result"}},
                {"message": {"role": "assistant", "content": "y" * 1200}},
            ],
            rd_type=RawDataType.AGENTCONVERSATION,
        )
        assert _should_skip_atomic_fact_for_agent(mc) is True


# ===========================================================================
# _clone_episodes_for_users
# ===========================================================================


class TestCloneEpisodesForUsers:
    """Replicate a single group Episode across participants."""

    def _make_state(self, participants):
        group_ep = EpisodeMemory(
            memory_type=MemoryType.EPISODIC_MEMORY,
            user_id="g1",
            timestamp=datetime(2026, 1, 1),
            subject="team",
            summary="shipped X",
            episode="The team shipped X.",
        )
        return SimpleNamespace(
            group_episode_memories=[group_ep], participants=participants
        )

    def test_clones_one_per_participant(self):
        state = self._make_state(["alice", "bob", "carol"])
        clones = _clone_episodes_for_users(state)
        assert len(clones) == 3
        assert {c.user_id for c in clones} == {"alice", "bob", "carol"}

    def test_user_name_set_to_user_id(self):
        state = self._make_state(["alice"])
        clones = _clone_episodes_for_users(state)
        assert clones[0].user_name == "alice"

    def test_episode_content_preserved_per_clone(self):
        """Each clone shares the source episode body — only user_id/name differ."""
        state = self._make_state(["alice", "bob"])
        clones = _clone_episodes_for_users(state)
        for clone in clones:
            assert clone.episode == "The team shipped X."
            assert clone.summary == "shipped X"


# ===========================================================================
# _save_agent_case (catch-all returning 0)
# ===========================================================================


class TestSaveAgentCase:
    """Audit-flagged catch-all: save failure → return 0, never raise.

    Phase 3 W7 will replace the bare ``except Exception`` with a typed
    hierarchy. Until then, the contract is pinned here so a refactor
    that propagates errors is a deliberate, reviewed change.
    """

    def _make_state(self, agent_case=None):
        return SimpleNamespace(
            agent_case=agent_case or _make_agent_case(),
            memcell=_make_memcell(),
            current_time=datetime(2026, 1, 1),
            request=SimpleNamespace(session_id="sess_1"),
        )

    @pytest.mark.asyncio
    async def test_save_success_returns_one(self):
        with (
            patch(
                "biz_layer.mem_memorize._convert_agent_case_to_doc",
                return_value=MagicMock(),
            ),
            patch(
                "biz_layer.mem_memorize.save_memory_docs",
                new=AsyncMock(return_value={}),
            ),
        ):
            count = await _save_agent_case(self._make_state())
        assert count == 1

    @pytest.mark.asyncio
    async def test_save_failure_returns_zero(self):
        """save_memory_docs failure must not propagate."""
        with (
            patch(
                "biz_layer.mem_memorize._convert_agent_case_to_doc",
                return_value=MagicMock(),
            ),
            patch(
                "biz_layer.mem_memorize.save_memory_docs",
                new=AsyncMock(side_effect=ConnectionError("ES down")),
            ),
        ):
            count = await _save_agent_case(self._make_state())
        assert count == 0

    @pytest.mark.asyncio
    async def test_doc_conversion_failure_returns_zero(self):
        """Failure earlier in the pipeline is also swallowed."""
        with patch(
            "biz_layer.mem_memorize._convert_agent_case_to_doc",
            side_effect=ValueError("bad agent case"),
        ):
            count = await _save_agent_case(self._make_state())
        assert count == 0
