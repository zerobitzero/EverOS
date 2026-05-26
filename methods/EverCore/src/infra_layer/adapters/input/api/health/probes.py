"""Readiness probes for downstream dependencies.

Each probe is a short async check that returns a ProbeResult. They are
executed in parallel by the health controller under a per-probe timeout
so a slow or hung downstream cannot block /readyz indefinitely.

Adding a new dependency is a matter of writing one async function that
raises on failure and registering it in `default_probes()`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from prometheus_client import Gauge as PrometheusGauge

from core.di.utils import get_bean_by_type
from core.observation.logger import get_logger
from core.observation.metrics.registry import get_metrics_registry

logger = get_logger(__name__)


# Per-dependency health gauge. 1 = healthy, 0 = unhealthy.
# Scraped by Prometheus; can drive alerts independent of `/readyz` polling.
#
# We bypass the BaseGauge wrapper (which is designed for auto-refresh) and
# bind a raw prometheus_client.Gauge to the project registry, so probe code
# can call set() directly.
_dependency_healthy = PrometheusGauge(
    name="evercore_dependency_healthy",
    documentation="1 if the named downstream is reachable, 0 otherwise.",
    labelnames=["name"],
    registry=get_metrics_registry(),
)


@dataclass
class ProbeResult:
    """Outcome of a single readiness probe."""

    name: str
    healthy: bool
    detail: Optional[str]
    latency_ms: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 2),
        }


Probe = Callable[[], Awaitable[None]]
"""A probe is an async callable that returns normally on success and raises
any exception on failure. It must not return a value."""


async def run_probe(name: str, check: Probe, timeout: float = 2.0) -> ProbeResult:
    """Run one probe under a timeout and capture its outcome.

    The probe contract is: succeed silently or raise. Timeouts and any
    other exception are converted into an unhealthy ProbeResult so the
    caller never has to wrap individual probes in try/except.
    """
    start = time.perf_counter()
    detail: Optional[str] = None
    healthy = False
    try:
        await asyncio.wait_for(check(), timeout=timeout)
        healthy = True
    except asyncio.TimeoutError:
        detail = f"timeout after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - boundary code: convert to result
        detail = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - start) * 1000

    _dependency_healthy.labels(name=name).set(1.0 if healthy else 0.0)
    if not healthy:
        logger.warning(
            "Readiness probe failed: name=%s detail=%s latency_ms=%.2f",
            name,
            detail,
            latency_ms,
        )
    return ProbeResult(name=name, healthy=healthy, detail=detail, latency_ms=latency_ms)


async def run_all(probes: List[tuple[str, Probe]], timeout: float = 2.0) -> List[ProbeResult]:
    """Run every probe in parallel and return all results in input order."""
    tasks = [run_probe(name, check, timeout=timeout) for name, check in probes]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


async def _probe_redis() -> None:
    from core.component.redis_provider import RedisProvider

    provider = get_bean_by_type(RedisProvider)
    client = await provider.get_client()
    await client.ping()


async def _probe_mongodb() -> None:
    from core.component.mongodb_client_factory import MongoDBClientFactory

    factory = get_bean_by_type(MongoDBClientFactory)
    wrapper = await factory.get_default_client()
    await wrapper.client.admin.command("ping")


async def _probe_elasticsearch() -> None:
    from core.component.elasticsearch_client_factory import ElasticsearchClientFactory

    factory = get_bean_by_type(ElasticsearchClientFactory)
    # The factory raises NotImplementedError on get_default_client; the
    # canonical accessor is register_default_client, which is idempotent
    # and returns the cached wrapper on subsequent calls.
    wrapper = await factory.register_default_client()
    if not await wrapper.async_client.ping():
        raise RuntimeError("elasticsearch ping returned False")


async def _probe_milvus() -> None:
    from core.component.milvus_client_factory import MilvusClientFactory

    factory = get_bean_by_type(MilvusClientFactory)
    client = factory.get_default_client()
    # Milvus client is synchronous; run on a thread to keep the event loop free.
    await asyncio.to_thread(client.list_collections)


def default_probes() -> List[tuple[str, Probe]]:
    """The standard set of downstream probes for /readyz.

    Add new probes here. The order shapes the response payload but not the
    overall healthy/unhealthy verdict — every probe runs unconditionally.
    """
    return [
        ("redis", _probe_redis),
        ("mongodb", _probe_mongodb),
        ("elasticsearch", _probe_elasticsearch),
        ("milvus", _probe_milvus),
    ]
