# Code-Quality Improvement Roadmap

**Project**: EverCore
**Drafted**: 2026-05-26
**Estimated duration**: 6-8 weeks
**Companion documents**:
- [`exception_handling_analysis.md`](./exception_handling_analysis.md) — audit data
- [`slo_definitions.md`](./slo_definitions.md) — SLO/SLI set (Phase 1 T1.5)

---

## 0. Status

| Phase | State | Notes |
|---|---|---|
| Phase 1: Observability | ✅ **Done** (2026-05-26) | P0 (T1.1 `/livez`+`/readyz`, T1.2 JSON logging, T1.3 typed error metrics) and P1 (T1.4 OpenTelemetry opt-in skeleton, T1.5 SLO doc) all merged. |
| Phase 2: Test foundation | ✅ **Done** (2026-05-26) | T2.1 pytest job in CI (non-blocking until tests are explicitly tagged), T2.2 coverage XML + artifact upload, T2.3 path-based test markers + `make test-unit/integration/e2e`, T2.5 typecheck in CI (continue-on-error), T2.6 benchmark scaffolding (opt-in, `make benchmark`), T2.4 unit-test backfill for `agentic_layer/memory_manager.py` and `biz_layer/mem_memorize.py` (`agent_skill_extractor.py` was already covered by 193 existing tests). One xfail surfaced — the audit-flagged possibly-unbound bug in `retrieve_mem`'s fallback path, queued for Phase 3 W7. |
| Phase 3: try-catch cleanup | 🟡 **Week 5 done** (2026-05-26) | W5 mechanical fixes merged: 20× `traceback.print_exc` → `logger.exception`, duplicate `RetryConfig` consolidated (longjob is canonical), `ruff G` + `BLE` rules enabled with baseline noqa markers. W6 (retry refactor) and W7 (catch-all + custom exceptions + large try-block splits) deferred — both require 24h staging soak per §4.2 and are unsafe to bundle into a doc-driven session. |

**Where this session stopped**: at the Phase 3 W5 / W6 boundary. Both
W6 (retry refactor) and W7 (catch-all + custom exception hierarchy +
splitting the 241-line try block) require a 24h staging soak per PR per
§4.2 and are unsafe to bundle into a doc-driven session.

Order of attack from here:
1. Phase 3 W6 — retry refactor (tenacity / circuit breaker).
2. Phase 3 W7 — custom exception hierarchy + catch-all overhaul +
   splitting the 241-line try block. The xfail in
   `tests/test_agentic_memory_manager.py::test_no_request_returns_empty_response`
   is the audit's named possibly-unbound bug; fixing it in W7 will
   flip the xfail to xpass.

---

## 1. Background and Strategy

### 1.1 Why now

The code audit (see [`exception_handling_analysis.md`](./exception_handling_analysis.md)) surfaced systemic issues:

- **Exception handling**: 781 try blocks, 71% are `except Exception` catch-alls, the largest try block is 241 lines
- **Retry strategy**: 6 copy-pasted HTTP retries, 5 LLM retries with **no sleep at all**, 2 parallel `RetryConfig` classes
- **Tests**: 68 test files and 47 K lines of test code — yet **CI does not run `pytest` at all**
- **Observability**: 94 Prometheus metrics exist, but **structured logging and distributed tracing are entirely missing**, and `/health` is only a shallow check

### 1.2 Three-Phase Strategy

```
┌────────────────────────────────────────────────────────────────────┐
│ Phase 1: Observability     (2 weeks)  →  Instrument before driving │
│         ↓                                                          │
│ Phase 2: Test foundation   (2 weeks)  →  Build the safety net      │
│         ↓                                                          │
│ Phase 3: try-catch cleanup (3 weeks)  →  Cut, measure the effect   │
└────────────────────────────────────────────────────────────────────┘
```

**Guiding principle: observe first, measure next, change last.**

**Why not in another order?**

- ❌ Refactor try-catch first with no metrics → no way to know whether things got better, error-rate movement is unattributable
- ❌ Refactor first with no tests → bugs surface only after deployment
- ❌ Add tests first with no metrics → tests don't know what "stable" should look like (no SLO baseline)

Each phase prepares the ground for the next. Each phase **delivers value on its own** — even stopping at Phase 1 leaves the system better than today.

### 1.3 Success Criteria (end state)

| Dimension | Current | Target |
|---|---|---|
| `except Exception` share | 71% | < 30% |
| Largest try block | 241 lines | < 50 lines |
| CI runs unit tests | ❌ | ✅ |
| Code coverage measured | Not measured | Report + 60% threshold |
| Structured logging | ❌ (plain strings) | ✅ JSON output |
| Distributed tracing | ❌ | ✅ OpenTelemetry |
| `/readyz` vs `/livez` | Not separated | Separated |
| Downstream health checks | ❌ | All 5 dependencies probed |

---

## 2. Phase 1: Observability (2 weeks)

**Goal**: Before changing any code, make the system's internal state **visible** and **measurable**.

### 2.1 Current State

✅ Already in place:
- 94 Prometheus metrics (memorize, retrieve, vectorize, rerank)
- `@trace_logger` decorator + `stage_timer`
- Request ID propagation through middleware
- 23 `contextvars` usages

❌ Missing:
- Structured logging (all 247 logger calls use plain f-strings)
- OpenTelemetry / distributed tracing
- Health check that doesn't validate downstreams
- SLO/SLI definitions
- Error metric labelled by `exception_type`

### 2.2 Deliverables

#### P0: Must-do (week 1)

**T1.1 Split health check into `/livez` and `/readyz`**
- File: `src/infra_layer/adapters/input/api/health/health_controller.py`
- Changes:
  - `/livez`: process heartbeat; no downstream checks; always 200
  - `/readyz`: verify Redis + MongoDB + Elasticsearch + Milvus + LLM provider can all be reached
  - Emit a separate metric `dependency_healthy{name="..."}` per downstream
- Acceptance: K8s probe configuration documented
- Effort: half a day

**T1.2 Structured logging (structlog)**
- Add `structlog>=24.1.0` to dependencies
- Replace the `get_logger` implementation in `core/observation/logger.py`
- Output format: coloured console in development, JSON in production
- Standard fields: `request_id`, `tenant_id`, `user_id`, `group_id`, `session_id`
- **Don't touch logger call sites yet** — keep backward compatibility; the migration is for Phase 3
- Effort: 1-2 days

**T1.3 Error metrics labelled by exception type**
- Update the existing `record_*_error` functions: change the `error_type` label from `'unknown'` to the concrete exception class name
- Add a `logger.exception` helper in `core/observation/logger.py` that automatically emits the metric
- Effort: half a day

#### P1: Strongly recommended (week 2)

**T1.4 OpenTelemetry integration**
- Dependencies:
  ```
  opentelemetry-api>=1.27.0
  opentelemetry-sdk>=1.27.0
  opentelemetry-instrumentation-fastapi
  opentelemetry-instrumentation-httpx
  opentelemetry-instrumentation-redis
  opentelemetry-instrumentation-pymongo
  opentelemetry-exporter-otlp
  ```
- Modify the `@trace_logger` decorator: emit real OTel spans, not just logs
- Configuration: `OTEL_EXPORTER_OTLP_ENDPOINT` env var, disabled by default
- Add Jaeger / Tempo to docker-compose
- Effort: 2-3 days

**T1.5 SLO/SLI definitions document**
- Create `docs/dev_docs/slo_definitions.md`
- Define:
  - `memorize_success_rate` (5xx rate < 0.1%)
  - `retrieve_p95_latency` (< 500 ms)
  - `llm_call_error_budget` (< 1% failures per month)
- Commit Grafana dashboard JSON to `docs/grafana/`
- Effort: 1 day

### 2.3 Phase 1 Acceptance

| Check | How to verify |
|---|---|
| `/livez` returns 200 with no downstream dependencies | `curl localhost:1995/livez` |
| `/readyz` returns 503 after Redis goes down | `docker stop memsys-redis && curl /readyz` |
| Default log output is JSON | `tail logs/*.log \| jq .` doesn't fail |
| Error metric `exception_type` label is non-empty | `curl /metrics \| grep memorize_error` |
| Jaeger UI shows a complete trace | Open `:16686` and trigger a memorize request |

### 2.4 Exit Criteria

✅ All P0 items plus at least one P1 item complete
✅ No production incidents
✅ The team can read Grafana / Jaeger (training done)

---

## 3. Phase 2: Test Foundation (2 weeks)

**Goal**: Build the "change-without-fear" safety net, especially in preparation for the semantic refactoring in Phase 3.

### 3.1 Current State

✅ Already in place:
- 68 test files, 47,061 lines of test code
- `pytest-asyncio` and `pytest-cov` are dev deps
- 785 fixture / mock usages
- 32 E2E tests (depend on docker-compose)

❌ Missing:
- **CI does not run tests** (biggest problem)
- No coverage report
- 32 of 68 tests are heavy E2E; unit-test share is too low
- Error-path tests are sparse (71% catch-all makes error paths untestable)
- No performance benchmarks

### 3.2 Deliverables

#### P0: Must-do (week 3)

**T2.1 CI runs pytest**
- Modify `.github/workflows/evercore-smoke.yml`: add a `test` job
- Start docker-compose services (Redis, MongoDB, ES, Milvus)
- Run `make test`
- Effort: 1 day (including CI debugging)

**T2.2 Enable coverage reporting with a threshold**
- Change `make test` to `pytest --cov=src --cov-report=xml --cov-report=term`
- Wire codecov or GitHub PR coverage comments into CI
- Treat the current baseline as the minimum (do not allow regression)
- Effort: half a day

**T2.3 Test classification markers**
- Tag the 68 tests with `@pytest.mark.unit` / `@pytest.mark.integration` / `@pytest.mark.e2e`
- CI runs `unit + integration` by default; E2E runs in its own job or manually
- Local `make test-unit` runs without docker
- Effort: 1-2 days

#### P1: Strongly recommended (week 4)

**T2.4 Backfill unit tests on critical paths**
- Priority modules (aligned with Phase 3 refactor priorities):
  - `biz_layer/mem_memorize.py` (home of the 241-line try)
  - `agentic_layer/memory_manager.py` (source of the possibly-unbound bug)
  - `memory_layer/memory_extractor/agent_skill_extractor.py` (Type B retry anti-pattern)
- Target: happy-path + at least 3 error paths per module
- Mock strategy: mock `LLMProvider` and repositories; **do not mock business logic**
- Effort: 3-5 days

**T2.5 Type checking in CI**
- Add `make typecheck` to CI (runs `ty check`)
- Treat the current 891 ty diagnostics as baseline; do not allow increases
- Effort: 30 minutes

**T2.6 Performance benchmark baseline**
- Add a `tests/benchmarks/` directory
- Use `pytest-benchmark`
- Key paths:
  - `retrieve_mem_hybrid` p50/p95 over 100 calls
  - End-to-end `memorize` latency for a single message
- Don't block CI yet (establish baseline first; add thresholds in 3 months)
- Effort: 1-2 days

### 3.3 Phase 2 Acceptance

| Check | How to verify |
|---|---|
| The `pytest` step in CI shows green | GitHub Actions page shows ✅ |
| Coverage report posted on each PR automatically | Open a test PR |
| `make test-unit` finishes locally in under 30 s | Time it |
| `make typecheck` does not report new errors | Run in CI |
| Line coverage of `biz_layer/mem_memorize.py` > 70% | Coverage report |

### 3.4 Exit Criteria

✅ All P0 items plus T2.4 (unit-test backfill) complete
✅ Coverage baseline established
✅ At least 3 PRs have been caught and fixed because of test failures (proving CI works)

---

## 4. Phase 3: try-catch Cleanup (3 weeks)

**Goal**: Put the tooling built in Phases 1 and 2 to work and **change real business code**. This is the highest-volume, highest-risk phase — but with the preparation done, the change is measurable and reversible.

### 4.1 Entry Conditions

✅ Phase 1 complete: error metrics labelled by type, tracing available
✅ Phase 2 complete: CI runs tests, coverage threshold enforced
✅ Critical modules have unit tests

### 4.2 Deliverables

See [`exception_handling_analysis.md` §9 Action Plan](./exception_handling_analysis.md). Execution cadence:

#### Week 5: Low-risk mechanical changes

Per the audit report, Sprint 1:
- Replace the 20 `traceback.print_exc()` with `logger.exception` / `exc_info=True`
- Enable `ruff G004` and clean up 247 f-string logger calls
- Merge the two `RetryConfig` classes
- Enable `ruff BLE001` baseline

Each item is its own PR, and **each PR compares Phase 1 metrics before/after** (error rate, counts per `exception_type`).

#### Week 6: Retry refactor

- Type A: 6 HTTP retries → tenacity decorator
- Type B: 5 LLM output-format retries → "feedback-retry"
- Type E: service-level fallback → add a lock or adopt `aiobreaker`

Each as its own PR, and **the retry-count metrics in Grafana should stay flat**.

#### Week 7: Business-layer catch-all overhaul

Per the audit report, Sprints 2-3:
- Introduce a custom exception hierarchy (`MemorizeError`, `RetrieveError`, `ExtractionError`)
- Convert business-layer catch-all to upward propagation
- Add boundary except blocks in controllers to catch the specific types
- Split the top-5 oversize try blocks

This is the riskiest week. **Every PR must**:
1. Pass full CI with all tests green
2. Not lower coverage
3. Run 24 hours in staging with error rate not rising

### 4.3 Phase 3 Acceptance

| Check | Current | Target |
|---|---|---|
| `except Exception` share | 71% | < 30% |
| `traceback.print_exc()` remaining | 20 | 0 |
| Largest try block (lines) | 241 | < 50 |
| logger f-string count | 247 | < 50 (keep legitimate uses like prompt templates) |
| `error_type='unknown'` share | ~80% | < 10% |

### 4.4 Exit Criteria

✅ All P0/P1 items complete
✅ Production error rate has not risen (30-day comparison)
✅ MTTR (mean time to repair) is at least flat or has fallen

---

## 5. Timeline Overview

```
Week 1-2  │ Phase 1: Observability
          │   W1: /livez+readyz, structlog, error metrics
          │   W2: OpenTelemetry, SLO document
          │
Week 3-4  │ Phase 2: Test foundation
          │   W3: CI pytest, coverage, test markers
          │   W4: Unit-test backfill, typecheck in CI, benchmark baseline
          │
Week 5-7  │ Phase 3: try-catch cleanup
          │   W5: Mechanical fixes (traceback / f-string / RetryConfig)
          │   W6: Retry refactor (tenacity / circuit breaker)
          │   W7: Catch-all + business exceptions + large try-block splits
          │
Week 8    │ Buffer / wrap-up / documentation
```

**Total effort estimate**: 8 weeks (one engineer full-time) or 12 weeks (half-time).

---

## 6. Risk and Rollback

### 6.1 Top Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Phase 1 structlog rollout breaks log format | Low | Medium | Canary release, keep the old logger as a fallback for 1 week |
| Phase 2 docker-based CI is slow | Medium | Low | Run unit and integration tests in parallel jobs |
| Phase 3 surfaces the real error rate | High | Medium | **That's a feature**; the rate was always there, just hidden. Notify oncall first. |
| Phase 3 retry changes affect LLM bill | Medium | Low | Add a retry-budget cap metric and alert on it |
| OpenTelemetry adds overhead | Low | Low | Default sampling rate 10%, configurable |

### 6.2 Per-Phase Rollback

- **Phase 1**: every change is additive (structlog, tracing, new endpoints). Each can be turned off behind a feature flag.
- **Phase 2**: CI changes roll back in seconds (`git revert` on the workflow yaml).
- **Phase 3**: every PR is independently revertable; critical changes stay behind feature flags.

---

## 7. How We'll Measure Improvement

The "success picture" for the 8-week effort:

```
Error visibility:
  error_type='unknown' share   80% → < 10%
  Sentry/alert grouping        coarse → fine-grained (by exception class)

Debug efficiency:
  MTTR median                   flat or downward
  Time to locate a root cause   minutes → seconds in Grafana

Code health:
  ruff baseline                 239 → < 50
  ty baseline                   891 → < 800 (just no growth)
  Coverage                      unknown → > 60%
  except Exception share        71% → < 30%

Engineering efficiency:
  Local unit-test feedback loop none → < 30 seconds
  CI-to-deploy                  manual → automatic
  "Dare I change one line?"     low → high
```

---

## 8. Explicitly Out of Scope

To keep this plan focused, the following are **not** part of this effort:

- ❌ Switching Python versions (stay on 3.12)
- ❌ Switching frameworks (stay on FastAPI / Pydantic v2)
- ❌ Data-model refactor (Pydantic schemas stay put)
- ❌ Redesigning the RAG algorithm
- ❌ Multi-region deployment / DR design
- ❌ Performance optimisation (unless Phase 3 happens to expose a clear bottleneck)

These are all worthwhile — they're just not part of the "code quality" main line.

---

## 9. Next Steps

1. **This week**: the team reviews the roadmap and locks owners / priorities
2. **Next Monday**: Phase 1 W1 kicks off; first PR is T1.1 `/livez` + `/readyz`
3. **Every Friday**: progress sync, risk review, decide whether ordering needs to shift

If you have questions or want to adjust ordering or drop items, leave a PR comment below or open a GitHub issue.

---

## Appendix: Related Documents

- [`exception_handling_analysis.md`](./exception_handling_analysis.md) — Full exception-handling and retry audit (data foundation)
- [`development_standards.md`](./development_standards.md) — Project development standards
- [`metrics_library_design.md`](./metrics_library_design.md) — Existing metrics library design
