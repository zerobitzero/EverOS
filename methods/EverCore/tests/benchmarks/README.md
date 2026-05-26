# EverCore Performance Benchmarks

Performance baselines for EverCore's hot paths. Phase 2 T2.6 of the
[code-quality roadmap](../../docs/dev_docs/code_quality_roadmap.md).

## Scope

Two paths are tracked:

| Benchmark | Target | Roadmap citation |
|---|---|---|
| `retrieve_mem_hybrid` p50/p95 over 100 calls | `MemoryManager.retrieve_mem_hybrid` | §3.2 T2.6 |
| End-to-end memorize latency for a single message | `biz_layer.mem_memorize.memorize` | §3.2 T2.6 |

## How to run

Benchmarks are **local-only by default** — they require docker-compose
services (Redis, MongoDB, Elasticsearch, Milvus, LLM provider). They are
explicitly **not** wired into CI yet; the roadmap calls for establishing
a baseline first and adding thresholds after roughly three months of
measurement.

```bash
# Bring up infrastructure
docker compose up -d

# Run benchmarks
make benchmark

# Save a baseline snapshot
uv run pytest tests/benchmarks/ --benchmark-save=baseline

# Compare against the saved baseline
uv run pytest tests/benchmarks/ --benchmark-compare=baseline
```

## Adding a benchmark

- Put the test under `tests/benchmarks/`.
- Use the `@pytest.mark.benchmark` marker (already registered in
  `pytest.ini`).
- Skip cleanly when prerequisites are absent — see existing files for
  the pattern.

## Why these specific paths

`retrieve_mem_hybrid` is the highest-traffic read path and the source of
the `retrieve_p95_latency` SLO target
([slo_definitions.md §3.2](../../docs/dev_docs/slo_definitions.md#32-slo-2-retrieve_p95_latency)).
End-to-end memorize touches LLM extraction, vectorisation, and three
storage layers — a useful integration-shape signal that catches
regressions individual unit tests would miss.
