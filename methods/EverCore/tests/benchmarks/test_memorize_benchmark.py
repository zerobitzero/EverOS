"""Benchmark: end-to-end memorize latency for a single message.

Targets ``biz_layer.mem_memorize.memorize``. Tied to the
``memorize_success_rate`` SLO (target ≥ 99.9%) and to the
``memorize_duration_seconds`` Prometheus histogram.

Run via ``make benchmark`` with infrastructure up.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.benchmark


@pytest.fixture(scope="session")
def memorize_callable():
    """Resolve the memorize entry point, skipping cleanly if unavailable."""
    if not os.environ.get("RUN_BENCHMARKS"):
        pytest.skip("RUN_BENCHMARKS not set; opt-in flag for live benchmarks")
    try:
        from biz_layer.mem_memorize import memorize
    except ImportError as exc:
        pytest.skip(f"Cannot import memorize: {exc}")
    return memorize


@pytest.fixture
def sample_memorize_request():
    """A single-message MemorizeRequest sized as a typical conversation turn."""
    try:
        from api_specs.dtos.memorize import MemorizeRequest, MessageItem
    except ImportError:
        pytest.skip("MemorizeRequest DTO not importable")
    return MemorizeRequest(
        user_id="bench_user",
        group_id="bench_group",
        session_id="bench_session",
        new_raw_data_list=[
            MessageItem(
                message_id="bench_msg_1",
                sender="bench_user",
                sender_name="Bench User",
                content="benchmark message body",
                type="text",
            )
        ],
    )


def test_memorize_end_to_end_latency(
    benchmark, memorize_callable, sample_memorize_request
):
    """Measure end-to-end memorize duration for one message."""
    import asyncio

    def _bench():
        return asyncio.run(memorize_callable(sample_memorize_request))

    benchmark(_bench)
