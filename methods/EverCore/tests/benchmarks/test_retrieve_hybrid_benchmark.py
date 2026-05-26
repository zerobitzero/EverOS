"""Benchmark: hybrid memory retrieval latency.

Targets ``MemoryManager.retrieve_mem_hybrid``. Tied to the
``retrieve_p95_latency`` SLO (target p95 < 500 ms; see
``docs/dev_docs/slo_definitions.md``).

This test is skipped automatically unless the required runtime
infrastructure (Redis, MongoDB, ES, Milvus, LLM provider) is up.
Run via ``make benchmark`` after ``docker compose up -d``.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.benchmark


@pytest.fixture(scope="session")
def memory_manager():
    """Resolve a real MemoryManager from the DI container.

    The DI container is constructed lazily via bootstrap; if the
    container cannot be brought up (env, deps), the entire benchmark
    is skipped rather than failing.
    """
    if not os.environ.get("RUN_BENCHMARKS"):
        pytest.skip("RUN_BENCHMARKS not set; opt-in flag for live benchmarks")
    try:
        from core.di.utils import get_bean_by_type
        from agentic_layer.memory_manager import MemoryManager
    except ImportError as exc:
        pytest.skip(f"Cannot import MemoryManager: {exc}")
    try:
        return get_bean_by_type(MemoryManager)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"DI container unavailable: {exc}")


@pytest.fixture
def sample_request():
    """Construct a representative RetrieveMemRequest.

    Kept minimal — real benchmark runs should override via parametrize
    with realistic payload sizes and tenant ids.
    """
    try:
        from api_specs.dtos.retrieve import RetrieveMemRequest
    except ImportError:
        pytest.skip("RetrieveMemRequest DTO not importable")
    return RetrieveMemRequest(
        user_id="bench_user",
        query="test query",
        top_k=10,
    )


def test_retrieve_mem_hybrid_p50_p95(benchmark, memory_manager, sample_request):
    """Measure retrieve_mem_hybrid over the default pytest-benchmark rounds.

    pytest-benchmark reports min/mean/median/p95 automatically. The
    roadmap target is p95 < 500 ms; do not yet enforce — record first.
    """
    import asyncio

    async def _run():
        return await memory_manager.retrieve_mem_hybrid(sample_request)

    def _bench():
        return asyncio.run(_run())

    benchmark(_bench)
