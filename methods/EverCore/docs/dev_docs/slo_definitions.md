# SLO / SLI Definitions

**Project**: EverCore
**Owner**: Platform / Observability
**Last reviewed**: 2026-05-26
**Companion documents**:
- [`code_quality_roadmap.md`](./code_quality_roadmap.md) — Phase 1 T1.5
- [`metrics_library_design.md`](./metrics_library_design.md) — metric naming and library

---

## 1. Purpose

This document is the **single source of truth** for what "healthy" means in
EverCore. It exists so that:

- On-call engineers know which alerts are load-bearing and which are noise.
- Refactoring work (notably Phase 3 of the code-quality roadmap) has a
  baseline to measure against — "did we make it better, worse, or neither?"
- Capacity, performance, and reliability work share the same yardsticks.

If an SLI is not listed here, treat it as informational. Only the SLIs in
this document are tied to error budgets, paging, and roadmap exit criteria.

---

## 2. Definitions

- **SLI** (Service Level Indicator): a quantitative measure of one aspect of
  service quality — e.g. "fraction of memorize requests that succeed".
- **SLO** (Service Level Objective): the target value or range for an SLI
  over a stated window — e.g. "≥ 99.9% over a rolling 30 days".
- **Error budget**: `1 - SLO`. The amount of failure that is acceptable
  before the SLO is breached. Used to gate risky changes.
- **Window**: every SLO below uses a **rolling 30-day** window unless
  otherwise stated. Short bursts of failure are tolerated; sustained
  failure is not.

---

## 3. The SLO Set

EverCore exposes three user-facing capabilities that warrant first-class
SLOs, plus one platform dependency SLO. Everything else is informational.

### 3.1 SLO-1: `memorize_success_rate`

**Statement**: At least **99.9%** of memorize requests succeed in a
rolling 30-day window.

| Field | Value |
|---|---|
| SLI source | `evermemos_agentic_memorize_requests_total` |
| Success definition | `status="success"` **or** `status="extracted"` **or** `status="accumulated"` |
| Failure definition | `status="error"` |
| SLO target | ≥ 99.9% |
| Error budget | 0.1% (≈ 43.2 min / 30 d) |
| Window | rolling 30 d |
| Severity | **page** at 50% budget burn / 6 h; **ticket** at 25% burn / 24 h |

**PromQL**:

```promql
# Success rate over 30d
(
  sum(rate(evermemos_agentic_memorize_requests_total{status=~"success|extracted|accumulated"}[30d]))
  /
  sum(rate(evermemos_agentic_memorize_requests_total[30d]))
)
```

**Notes**:
- `accumulated` is success — it means the message was queued for later
  extraction, not that anything failed.
- Per-tenant rollups are available via the `space_id` label; do not include
  `space_id` in the SLO numerator/denominator (high cardinality, and a
  single noisy tenant should not move the global SLO).

### 3.2 SLO-2: `retrieve_p95_latency`

**Statement**: The **p95 end-to-end latency** of memory retrieval stays
**below 500 ms** in a rolling 30-day window.

| Field | Value |
|---|---|
| SLI source | `evermemos_agentic_retrieve_duration_seconds` (Histogram) |
| Latency definition | server-side wall-clock, from controller entry to response serialised |
| SLO target | p95 < 500 ms |
| Error budget | 5% of buckets above the threshold (informally; latency budgets are time-burn based) |
| Window | rolling 30 d |
| Severity | **page** when p95 > 1 s for 10 min; **ticket** when p95 > 500 ms for 1 h |

**PromQL**:

```promql
# p95 over 30d, all memory types and retrieval methods
histogram_quantile(
  0.95,
  sum by (le) (rate(evermemos_agentic_retrieve_duration_seconds_bucket[30d]))
)
```

**Notes**:
- Slice by `retrieve_method` (`vector`, `hybrid`, `rrf`, `agentic`, …) for
  diagnosis. `agentic` retrieval is expected to be slower; an `agentic`-only
  regression is not necessarily a global p95 regression.
- Stage-level latency lives in
  `evermemos_agentic_retrieve_stage_duration_seconds`; use it during
  incidents, not for the SLO itself.

### 3.3 SLO-3: `llm_call_error_budget`

**Statement**: At most **1%** of LLM API calls fail in a rolling 30-day
window. Counts retries collapsed — i.e. one call = one outcome after the
provider's last retry.

| Field | Value |
|---|---|
| SLI source | `evermemos_memory_layer_llm_requests_total` |
| Success definition | `status="success"` |
| Failure definition | any of: `rate_limit`, `key_error`, `server_error`, `client_error`, `request_error` |
| SLO target | error rate < 1% |
| Error budget | 1% (i.e. up to 1 failure per 100 calls) |
| Window | rolling 30 d |
| Severity | **page** at `rate_limit` > 5% / 10 min (provider quota issue); **ticket** at total error rate > 1% / 1 h |

**PromQL**:

```promql
# Error rate over 30d, all models
(
  sum(rate(evermemos_memory_layer_llm_requests_total{status!="success"}[30d]))
  /
  sum(rate(evermemos_memory_layer_llm_requests_total[30d]))
)
```

**Notes**:
- `rate_limit` and `key_error` indicate **provider-side** issues — quota,
  billing, key rotation. They share the budget with code-side failures
  because both visibly degrade user experience; the breakdown still helps
  diagnose root cause.
- Slice by `model` to see whether a single model is dragging the budget.

### 3.4 SLO-4 (platform): `dependency_availability`

**Statement**: Each downstream dependency probed by `/readyz` is healthy
**≥ 99.95%** of the time over a rolling 30-day window.

| Field | Value |
|---|---|
| SLI source | `evercore_dependency_healthy{name="..."}` (Gauge) |
| Dependencies | `redis`, `mongodb`, `elasticsearch`, `milvus`, `llm` |
| Healthy definition | gauge value `== 1` |
| SLO target | ≥ 99.95% per dependency |
| Window | rolling 30 d |
| Severity | **page** when any dependency is unhealthy for > 5 min; **ticket** when the gauge flaps (> 3 transitions / 10 min) |

**PromQL**:

```promql
# Per-dependency availability over 30d
avg_over_time(evercore_dependency_healthy[30d])
```

**Notes**:
- This SLO measures **EverCore's view** of the dependency, not the
  dependency itself. A network partition between EverCore and Redis
  counts against this SLO even if Redis is fine.
- The `name` label values come from
  `infra_layer/adapters/input/api/health/probes.py` — keep this list in
  sync if probes are added or renamed.

---

## 4. What is *not* an SLO

To keep the SLO set load-bearing, the following are explicitly
**informational only** — observe them, do not page on them:

- `boundary_detection_total`, `memcell_extracted_total`,
  `memory_extracted_total` — quality signals, not availability signals.
- `retrieve_results_count` — product-quality metric; "zero results" is not
  always an error.
- `memory_extraction_stage_duration_seconds`,
  `retrieve_stage_duration_seconds` — diagnosis tools, not SLOs. Owning a
  stage SLO would couple alerts to the current pipeline shape, which we
  expect to evolve.
- Memory-pressure or queue-depth gauges — capacity signals; alert on
  saturation, not on the gauge itself.

When tempted to "promote" one of these to an SLO, ask: *can a user tell
when this is broken, and would they consider it broken?* If not, leave it
informational.

---

## 5. Error Budget Policy

Each user-facing SLO (SLO-1, SLO-2, SLO-3) has a 30-day error budget.

- **Budget ≥ 50% remaining**: ship freely. Risky changes are acceptable.
- **Budget 25–50% remaining**: prefer reversible changes (feature flags,
  canaries). Phase 3 refactors land behind flags only.
- **Budget < 25% remaining**: freeze non-critical changes. Focus on
  reliability work until the burn rate flattens.
- **Budget exhausted**: stop feature work. The next merge must improve
  the SLI driving the burn.

The roadmap's **Phase 3** explicitly checks SLO health before each merge
(see [`code_quality_roadmap.md` §4.2](./code_quality_roadmap.md#42-deliverables)).

---

## 6. Verification

These checks should pass before declaring SLOs operational:

```bash
# 1. Memorize success-rate metric is being recorded with the expected labels
curl -s localhost:1995/metrics | grep '^evermemos_agentic_memorize_requests_total'

# 2. Retrieve latency histogram has the expected buckets
curl -s localhost:1995/metrics | grep '^evermemos_agentic_retrieve_duration_seconds_bucket'

# 3. LLM request counter is using a non-"unknown" status (T1.3 acceptance)
curl -s localhost:1995/metrics | grep '^evermemos_memory_layer_llm_requests_total'

# 4. Dependency gauges expose one series per downstream
curl -s localhost:1995/metrics | grep '^evercore_dependency_healthy'
```

A Grafana dashboard skeleton lives under `docs/grafana/`. Import it into
Grafana and point it at the Prometheus scraping `/metrics`. Per-tenant
breakdowns are intentionally omitted from the default dashboard to keep
cardinality controllable.

---

## 7. Change Process

- Adding or removing an SLO requires a PR that touches this document and
  the relevant Grafana dashboard JSON in `docs/grafana/`.
- Tightening a target (lower latency, higher success rate) is a soft
  change — open a PR, no separate review needed.
- Loosening a target (higher latency, lower success rate) requires
  agreement from at least one other maintainer and a note explaining the
  underlying constraint (e.g. provider rate-limit changes).
- All four SLOs are reviewed at the end of every roadmap phase and at
  least once per quarter.

---

## 8. References

- [`metrics_library_design.md`](./metrics_library_design.md) — naming,
  label conventions, registry singleton.
- [`exception_handling_analysis.md`](./exception_handling_analysis.md) §3 —
  why `error_type='unknown'` was previously dominant, and how Phase 1
  T1.3 fixed it.
- `src/infra_layer/adapters/input/api/health/probes.py` — concrete list of
  dependencies probed by `/readyz` and emitted as
  `evercore_dependency_healthy`.
