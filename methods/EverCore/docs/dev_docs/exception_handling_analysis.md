# Exception Handling Audit Report

**Project**: EverCore (`methods/EverCore/`)
**Audit date**: 2026-05-26
**Audit scope**: All of `src/**/*.py`
**Tools used**: `grep` / `ruff` / `ty` / Python AST

---

## 1. TL;DR

| Metric | Value | Assessment |
|---|---|---|
| Total `try` blocks | **781** | High |
| Total functions | **2,448** | — |
| **`try` blocks / function ratio** | **32%** | Industry RAG/Agent projects typically sit at 10-20% |
| `except Exception` share | **555 / 781 = 71%** | 🔴 Severely elevated; healthy range is < 30% |
| `logger` calls using f-strings | **247** | 🟡 CPU waste + inconsistent with structured logging |
| `traceback.print_exc()` left in code | **20** | 🟡 Bypasses the logger pipeline |
| Largest single `try` block | **241 lines** (`mem_memorize.py:118`) | 🔴 Practically wraps the entire function |
| Average `try` block size | 18.9 lines | Too large (ideal < 5 lines) |
| Hand-written retry loops (`for attempt in range`) | **17** | 🟡 None use `tenacity` — wheels reinvented |
| `2 ** attempt` backoff with no jitter | **~10** | 🔴 Thundering-herd risk |
| Duplicated `RetryConfig` classes | **2** | 🟡 One in `core/longjob`, one in `core/asynctasks` |

**Bottom line**: The codebase exhibits widespread "defensive-programming overreach". From CPython 3.11 onward `try` blocks themselves carry **zero overhead** on the happy path, so this **is not a performance problem** — it is a **correctness and observability problem**: errors are silently swallowed at scale, bugs struggle to surface, and the system runs in a degraded state without callers being aware. Retry handling is in equally bad shape: six near-identical copies of exponential backoff, five LLM retries with **no sleep whatsoever**, and two parallel `RetryConfig` classes coexisting.

---

## 2. Data Portrait

### 2.1 Density by Architectural Layer

| Layer | `try` blocks | Functions | Density | Assessment |
|---|---|---|---|---|
| `biz_layer` | 33 | 46 | **72%** | 🔴 The business layer should not be this high |
| `infra_layer` | 190 | 262 | **73%** | 🟡 Boundary code (DB/ES/Milvus) running high is somewhat expected, but the except shapes need work |
| `memory_layer` | 54 | 200 | 27% | 🟢 Acceptable |
| `agentic_layer` | 50 | 202 | 25% | 🟢 Acceptable |
| `core` | 359 | 1,426 | 25% | 🟢 Acceptable |
| `service` | 8 | 43 | 19% | 🟢 Good |
| `common_utils` | 15 | 85 | 18% | 🟢 Good |
| `api_specs` | 9 | 65 | 14% | 🟢 Good (a DTO layer should be light) |

**Key observation**: `biz_layer` is the standout anomaly. The business-logic layer is supposed to let exceptions propagate naturally to its callers (controllers), not absorb them itself.

### 2.2 Top 10 Files by `try` Density

| File | `try` blocks |
|---|---|
| `infra_layer/.../memcell_raw_repository.py` | 24 |
| `core/queue/.../redis_msg_group_queue_manager.py` | 23 |
| `biz_layer/mem_memorize.py` | 20 |
| `agentic_layer/retrieval_utils.py` | 14 |
| `infra_layer/.../raw_message_repository.py` | 13 |
| `devops_scripts/i18n/i18n_tool.py` | 13 |
| `core/oxm/es/base_repository.py` | 13 |
| `core/component/redis_provider.py` | 13 |
| `infra_layer/.../agent_case_raw_repository.py` | 12 |
| `core/oxm/milvus/milvus_collection_base.py` | 12 |

### 2.3 Top 5 Largest `try` Blocks (the "over-wrapping" anti-pattern)

| File:line | Size | Nature |
|---|---|---|
| `biz_layer/mem_memorize.py:118` (`_trigger_clustering`) | **241 lines** | The whole function body is wrapped |
| `core/queue/msg_group_queue/msg_group_queue_manager.py:553` | 182 lines | |
| `core/lock/redis_distributed_lock.py:67` | 175 lines | |
| `agentic_layer/memory_manager.py:617` (`get_vector_search_results`) | 153 lines | Essentially the entire function body |
| `memory_layer/memory_extractor/agent_case_extractor.py:594` | 148 lines | |

---

## 3. Anti-Pattern Catalogue (with code references)

### Anti-Pattern 1: Catch-all swallows real bugs into a black hole

**Symptom**: A large block of logic is wrapped in `except Exception`; the only response is `logger.error` followed by returning a "safe default".

**Example 1**: `biz_layer/mem_memorize.py:1857-1860`
```python
try:
    for memcell in memcells:
        await process_memory_extraction(memcell, ...)
    return memories_count
except Exception as e:
    logger.error(f"[mem_memorize] ❌ Memory extraction failed: {e}")
    traceback.print_exc()
    return 0   # ← controller treats 0 as "no new memories"; actually it's a failure
```

The API side at `memory_controller.py:165` consumes this return value to decide status:
```python
status = 'extracted' if memory_count > 0 else 'accumulated'
```

**Effect**: A write failure becomes HTTP 200 + `"status": "accumulated"` — **data is lost with no alarm signal**.

**Historical cost**: The `milvus_start` `NameError` bug found in an earlier audit (`memory_manager.py:782`) was swallowed by exactly this pattern. Code that should have crashed only emitted a single `error` log line in production, while the original exception was fully concealed.

---

### Anti-Pattern 2: Nested-recovery try — Russian-doll handlers

**Symptom**: An `except` block contains its own "recovery operation" wrapped in another try; if the recovery also fails, the failure is swallowed again.

**Example**: `biz_layer/mem_memorize.py:528-556` `_trigger_profile_extraction`
```python
try:
    # ... main profile-extraction logic ...
except Exception as e:
    logger.error(f"[Profile] ❌ Profile extraction failed: {e}", exc_info=True)
    try:
        # ← nested try: advance last_updated_ts on failure to avoid infinite re-selection
        for uid in user_id_list:
            await profile_repo.upsert(...)
    except Exception as ts_err:
        logger.warning(f"[Profile] Failed to advance last_updated_ts: {ts_err}")
        # ← the second-level failure is silently swallowed
```

**Problem**: The control flow reads like recursion. Once the second `except` fires, no signal escapes; the problem is permanently invisible.

---

### Anti-Pattern 3: Exceptions downgraded to "fake success"

**Symptom**: The function returns `None` / `[]` / `0` / `False` / the original input on error, so callers cannot distinguish "no data" from "fetch failed".

**Project-wide scan**: high concentration under `memory_extractor/`:

| File:line | Returns on failure |
|---|---|
| `foresight_extractor.py:320` | `[]` |
| `foresight_extractor.py:358` | `None` |
| `foresight_extractor.py:383` | `None` |
| `agent_skill_extractor.py:295` | `None` |
| `agent_skill_extractor.py:336` | `None` |
| `agent_skill_extractor.py:382` | `None` |
| `agent_skill_extractor.py:788` | `[]` |
| `episode_memory_extractor.py:403` | `None` |

**Specific case**: `biz_layer/mem_memorize.py:1432-1436` `preprocess_conv_request`:
```python
except Exception as e:
    logger.error(f"[preprocess] Data read failed: {e}")
    traceback.print_exc()
    # Use original request if read fails
    return request   # ← returns the unprocessed request as if it had been processed
```

Downstream code receiving `request.history_raw_data_list` may see an empty list or stale data with no metric or exception telling it so.

---

### Anti-Pattern 4: Over-wrapped try

**Canonical example**: `biz_layer/mem_memorize.py:118` `_trigger_clustering` — a **241-line** try that wraps the entire function body.

Meaning: any line inside the function that throws enters the same `except`, and there is no way to distinguish "DI lookup failed" from "clustering computation failed" from "database write failed". When you go to fix something, you can only guess.

**Ideal granularity**: a try should hug **one** specific external call that can fail (DB / API / IO). For example:

```python
# Don't do this
try:
    config = load_config()       # ← can't fail
    data = fetch_from_db()       # ← can fail
    result = process(data)       # ← pure compute, shouldn't be in try
    save_to_db(result)           # ← can fail
except Exception:
    pass

# Do this
config = load_config()
try:
    data = fetch_from_db()
except DBError as e:
    raise FetchFailure(...) from e

result = process(data)

try:
    save_to_db(result)
except DBError as e:
    raise PersistenceFailure(...) from e
```

---

### Anti-Pattern 5: Defending against exceptions that cannot happen

**Symptom**: a try wraps pure-Python operations (dict lookups, string strip, list indexing) that don't raise.

**Example**: `agentic_layer/retrieval_utils.py:233-241` (already changed to `except Exception:`, but the try itself remains)
```python
for mem in candidates:
    try:
        doc_vec = np.array(mem.extend.get("embedding", []))
        if len(doc_vec) > 0:
            doc_norm = np.linalg.norm(doc_vec)
            if doc_norm > 0:
                sim = np.dot(query_vec, doc_vec) / (query_norm * doc_norm)
                scores.append((mem, float(sim)))
    except Exception:
        continue   # ← wrapping a numpy dot product in try-except is over-defensive
```

`np.dot` does not raise on shape-matching float arrays. This try is really shielding **type errors**, but type errors should be allowed to crash and be caught by the type checker (ty/pyright), not silently skipped at runtime.

---

### Anti-Pattern 6: `traceback.print_exc()` bypassing structured logging

**20 occurrences remain**. This function:
- Writes directly to stderr
- Does not flow through the logger pipeline, so it never reaches structured logs / alerting systems
- May be lost in containerised environments where stderr/stdout is redirected

**Right way**:
```python
# Anti-pattern
except Exception as e:
    logger.error(f"failed: {e}")
    traceback.print_exc()

# Correct
except Exception as e:
    logger.error("failed: %s", e, exc_info=True)
```

`exc_info=True` hands the full stack to the logger formatter, which uniformly delivers it into structured logs.

---

### Anti-Pattern 7: f-strings inside `logger` calls

**247 occurrences project-wide.**
```python
# Anti-pattern
logger.debug(f"query_words: {query_words}")  # the f-string is evaluated even if DEBUG is off

# Correct
logger.debug("query_words: %s", query_words)  # interpolation only when DEBUG is enabled
```

**Performance angle**: every call interpolates the string (formatting `repr()` of lists/dicts is real CPU work). On hot paths it accumulates, but **the per-call cost is small**.

**Consistency angle**: `run.py` uses `%`-style while the other 99% of modules use f-strings. This is a team-convention issue.

---

## 4. Retry-Strategy Audit

If the previous anti-patterns are "one-shot try gone wrong", retries are "repeated try gone wrong" — more systemic and more hidden.

### 4.1 Overall Picture

| Type | Nature | Occurrences | Main problem |
|---|---|---|---|
| **A. HTTP/API call retry** | Infrastructure layer | ~8 | Copy-pasted `2**attempt`, no jitter, no exception-type distinction |
| **B. LLM output-format retry** | Business layer | 5 | **No sleep at all**, immediate retry — burns money + stacks with A |
| **C. Distributed-lock acquire retry** | Lock contention | 1 | Reasonable; no issue |
| **D. Background-task retry** (ARQ) | Worker framework | 2 separate `RetryConfig` classes | Wheel reinvented |
| **E. Service-level fallback** (pseudo-retry) | Failover | 2 | Lock-free counters; race conditions |
| **F. Loop-local try/continue** ("skip-on-failure") | Batch processing | ~6 | Partial failures are invisible |

**Every retry is hand-written; zero `tenacity` usage**:

```bash
grep -rn "from tenacity\|@retry\b" src/ --include="*.py" | wc -l   # → 0
```

### 4.2 Type A: HTTP/API Call Retry (copy-paste)

**Template**: `for attempt in range(max_retries): try ... except: await asyncio.sleep(2**attempt)`

| File:line | Retries | Backoff | Jitter | Exception-type discrimination |
|---|---|---|---|---|
| `core/component/llm/llm_adapter/anthropic_adapter.py:88` | `self.max_retries` | `2**attempt` | ❌ | ✅ (5xx retried, 4xx re-raised) |
| `core/component/llm/llm_adapter/gemini_adapter.py:63` | `self.max_retries` | `2**attempt` | ❌ | ❌ |
| `core/component/llm/llm_adapter/gemini_client.py:109` | `self.max_retries` | `2**attempt` | ❌ | ❌ |
| `agentic_layer/vectorize_base.py:108` | `config.max_retries` | `2**attempt` | ❌ | ❌ |
| `agentic_layer/rerank_deepinfra.py:104` | `config.max_retries` | `2**attempt` | ❌ | ❌ |
| `agentic_layer/rerank_vllm.py:...` | `config.max_retries` | `2**attempt` | ❌ | ❌ |

**Representative code** (`gemini_adapter.py:63-85`):
```python
for attempt in range(self.max_retries):
    try:
        response = await self.client.aio.models.generate_content(...)
        return self._convert_gemini_response(response, request.model)
    except Exception as e:
        if attempt == self.max_retries - 1:
            raise RuntimeError(
                f"An unexpected error occurred in GeminiAdapter: {e}"
            ) from e
        await asyncio.sleep(2**attempt)
```

**Problem analysis**:
1. **Copy-paste**: 6 places, nearly character-for-character identical. Maintenance cost ×6; any bug fix must be applied 6 times.
2. **No jitter**: when N concurrent requests are rate-limited at once they will retry in lock-step → a thundering herd that re-hammers the backend.
3. **No exception-type discrimination** (except Anthropic): 401 (auth) / 400 (malformed request) also gets retried N times, wasting quota.
4. **Loses the cause chain on failure**: `raise RuntimeError(f"...{e}") from e` does use `from`, but the original exception class (e.g. `APITimeoutError` vs. `RateLimitError`) is buried inside a string.
5. **2**0 = 1 s, 2**4 = 16 s**: with `max_retries=5`, the final wait is 32 s — that coroutine is blocked on the event loop for a long time.

### 4.3 Type B: LLM Output-Format Retry (business layer, no sleep)

**The worst-offender pattern**. Representative: `agent_skill_extractor.py:325`

```python
for attempt in range(3):
    try:
        resp = await self.llm_provider.generate(prompt)
        data = parse_json_response(resp)
        if data and isinstance(data.get("operations"), list):
            return data
        logger.warning(f"... retry {attempt + 1}/3: invalid format")
    except Exception as e:
        logger.warning(f"... retry {attempt + 1}/3: {e}")
return None   # ← all 3 attempts failed → silently return None
```

| File:line | Retries | Sleep | Return on failure |
|---|---|---|---|
| `memory_layer/memory_extractor/agent_skill_extractor.py:325` | 3 | **0** | `None` |
| `memory_layer/memory_extractor/agent_case_extractor.py:371` | 2 | **0** | `None` |
| `memory_layer/memory_extractor/agent_case_extractor.py:416` | 2 | **0** | `None` |
| `memory_layer/cluster_manager/manager.py:636` | 3 | **0** | `None` |
| `memory_layer/profile_manager/manager.py:178` | `max_retries` | `0.5 * (attempt+1)` (**linear**) | — |

**Problem analysis**:
1. **Type B has no sleep at all**: LLMs are high-latency (seconds) and billed per call. **Three back-to-back requests** = real money burned + a high chance of being stuck on the same prompt → same wrong result (LLMs are largely deterministic).
2. **Should feed the error back into the prompt**: when the format is wrong, don't retry the same prompt — append "previous reply + error description" so the LLM can correct itself.
3. **Silent `return None` on failure**: callers receive `None` and read it as "no content"; they can't tell "the model genuinely produced nothing" from "the model malformed 3 times in a row".
4. **Type B stacks on top of Type A**: A already retries 3-5 times at the HTTP layer, B retries 3 more at the business layer → a single business call can fire up to **3 × 3 = 9 LLM calls**. There is no total-budget control.
5. **Backoff strategies conflict**: `profile_manager` uses linear `0.5*(attempt+1)` (0.5 s, 1 s, 1.5 s …), inconsistent with the exponential backoff used elsewhere. Two engineers wrote two policies.

### 4.4 Type D: Two duplicated `RetryConfig` classes

| Location | Purpose |
|---|---|
| `core/longjob/interfaces.py:183` | `class RetryConfig` for the longjob consumer |
| `core/asynctasks/task_manager.py:54` | `class RetryConfig` for the ARQ `@task` decorator |

The two classes have **heavily overlapping fields** (`max_retries`, `exponential_backoff`, …) but are defined independently.

**Impact**: every bug fix touches two places; new retry-policy fields must be added twice; test coverage is split.

**Fix**: extract a single `core/retry/config.py` and have both call sites reference it.

### 4.5 Type E: Service-Level "Circuit Breaker" (crude implementation)

`HybridVectorizeService` / `HybridRerankService`:

```python
self._primary_failure_count: int = field(default=0, init=False, repr=False)
self.max_primary_failures: int = 3
```

Logic: after N consecutive failures on the primary provider, switch to the fallback.

**Problems** (also flagged in a previous audit):
1. `_primary_failure_count` is an **instance attribute with no lock**.
2. When multiple coroutines call `execute_with_fallback` concurrently there is a race condition — the counter can under- or over-count.
3. **No half-open state**: once you switch to fallback, the primary is never probed again.
4. **No timeout / reset window**: a single morning hiccup → the fallback is used all day (even after the primary has recovered).

This isn't really "retry" — it's a crude circuit breaker. Use a library such as `aiobreaker` or `purgatory`.

### 4.6 Type F: Loop-local try/continue ("skip on failure")

| File | Behaviour | Emits metric? |
|---|---|---|
| `core/cache/redis_cache_queue/redis_length_cache_manager.py:480-487` | Batch processing skips one failing item and continues | ❌ only `logger.warning` |
| `core/cache/redis_cache_queue/redis_windows_cache_manager.py:465-472` | Same | ❌ |
| `agentic_layer/retrieval_utils.py:233-241` | Skip on vector-dot failure | ❌ |

**Problem**: callers receive a partial result with **no way to tell "all succeeded" from "50% failed"**. Add a `partial_failure_count` metric.

### 4.7 Worked example: stacked retry amplification

Picture a `memorize()` call that triggers LLM extraction:

```
controller.add_memories()
└─ memory_manager.memorize()
   └─ AgentSkillExtractor._call_llm() ← Type B: for attempt in range(3), no sleep
      └─ LLMProvider.generate()
         └─ AnthropicAdapter.chat() ← Type A: for attempt in range(max_retries) with 2**attempt
            └─ httpx.post() ← httpx has no built-in retry
```

Worst-case call count:
- Type A `max_retries=3` → 1 + 2 + 4 = 7 s of waiting, 3 calls
- Type B `retries=3`, no sleep → 3 outer attempts
- **Final: 3 × 3 = 9 HTTP requests**, longest wait ~21 s (each outer attempt waits up to 7 s)

And:
- On total failure, **only `None` is returned** → controller sees `0 memories extracted`
- The client sees HTTP 200 and assumes "no new memories"
- Real story: 9 retries all failed, and the bill has been charged 9 × token cost

### 4.8 Retry-Refactor Recommendation: Standardise on `tenacity`

**One decorator replaces 6 copy-pasted blocks**:

```python
from tenacity import (
    retry, retry_if_exception_type, stop_after_attempt,
    wait_exponential_jitter, before_sleep_log,
)

@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, asyncio.TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=10, jitter=2),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _call_llm(...):
    response = await client.post(...)
    response.raise_for_status()  # ← 5xx raises and is retried; 4xx raises and is filtered out by retry_if_exception_type
    return response.json()
```

Benefits:
- 6 boilerplate copies → 1 decorator
- Jitter baked in (solves thundering herd)
- `retry_if_exception_type` gives precise control (4xx isn't retried, saving quota)
- `RetryError` on final failure carries the full attempt history (cause chain preserved)
- `before_sleep_log` emits a log entry per retry — no need for manual `logger.warning`

**Refactor for Type B LLM output-format retry**:

```python
# Anti-pattern: hard-retry the same prompt
for attempt in range(3):
    resp = await llm.generate(prompt)
    if valid(resp): return resp
return None

# Correct: feed the error signal back into the prompt
attempt_history = []
for attempt in range(3):
    enriched_prompt = prompt + format_correction_hints(attempt_history)
    resp = await llm.generate(enriched_prompt)
    if validation := validate(resp):
        return resp
    attempt_history.append((resp, validation.errors))
raise InvalidLLMOutputError(attempt_history)
```

Or even cleaner — **don't retry at all**, just raise and let the upper layer decide (re-run with a stronger model, downgrade, hand off to a human):

```python
resp = await llm.generate(prompt)
data = parse_json_response(resp)
if not (data and isinstance(data.get("operations"), list)):
    raise InvalidLLMOutputError(resp)
return data
```

### 4.9 Retry-Refactor Action List

| Priority | Item | Effort | Risk |
|---|---|---|---|
| P0 | Merge the two `RetryConfig` classes | 1 PR | Low |
| P0 | Type A: wrap the 6 HTTP retries with one tenacity decorator | 1 week | Medium (needs tests) |
| P1 | Type B: add sleep + jitter to the 5 LLM-format retries, or switch to "feedback retry" | Medium | Medium |
| P1 | Type E: introduce `aiobreaker` or lock the fallback counters | 1 PR | Medium |
| P2 | Type F: add a `partial_failure_count` metric to loop-continue paths | Small | Low |
| P3 | Global retry budget (total-time / total-call caps) | Design | Large |

---

## 5. Performance Impact

### 5.1 The `try` block itself ≈ free

CPython 3.11 shipped **"zero-cost exceptions"**:
- Old mechanism: entering `try:` emitted a `SETUP_FINALLY` opcode and ran a few instructions per execution
- New mechanism: the exception-handling table is moved out of bytecode into a side table; the happy path executes **zero exception-related instructions**
- Only the unwinding path runs when an exception is **actually raised**

This project targets `requires-python = ">=3.12,<3.13"`, so **all 781 `try` blocks add zero overhead on the happy path**.

### 5.2 Cost when an exception actually fires

Each raise/catch costs roughly 10–100 μs (depending on stack depth), spent on:
- Building the traceback object
- Walking the stack frames
- Formatting the exception message

But the project's most expensive operations are:
- LLM API calls: **hundreds of ms to several seconds**
- Milvus vector search: **tens to hundreds of ms**
- Elasticsearch queries: **tens of ms**

Tens of microseconds of exception cost is **entirely swallowed by network latency**.

### 5.3 What actually burns CPU is the side-work inside `except`

| Operation | Cost per call | Occurrences | Cumulative impact |
|---|---|---|---|
| `traceback.print_exc()` | ms-level | 20 | Medium |
| f-string in `logger.debug` | μs per call | 247 | High (every request × N) |
| `traceback.format_exc()` + string concatenation | ms-level | Scattered | Medium |

### 5.4 Hidden performance cost: side effects on the error path

**Example 1**: Retrying immediately with no backoff after an exception
- When the backend is rate-limited (OpenRouter, DeepInfra), no-backoff retries get you banned faster.
- Use `tenacity` or explicit exponential backoff.

**Example 2**: Falling back to a full scan / full recompute on exception
- When `preprocess_conv_request` fails, it returns the original request; downstream code may then run a much larger batch.
- The "recovery" is more expensive than the failure.

### 5.5 Performance Verdict

**Density itself is not a performance problem.** What needs fixing is:
1. f-strings in logger calls (247 cases) — machine-detectable via ruff `G004`
2. `traceback.print_exc()` (20 cases) — replace by hand or via ruff `T201`
3. "Recovery" logic on error paths (no-backoff retries, full fallbacks) — design-level review

---

## 6. Correctness Impact (the part that really matters)

### 6.1 Signal-Erosion Chain

```
   Original exception (precise, locatable)
        ↓ Caught by catch-all
   logger.error("failed")
        ↓ return None / [] / 0
   Upper layer sees "empty result"
        ↓ Treats it as "no data"
   API returns 200 + "accumulated"
        ↓ Client
   "Everything looks fine"
```

Every layer of `except Exception: log; return default` drops a level of signal. By the time the request reaches the API boundary you can no longer tell:
- Genuinely no data
- Interface failure
- Programming bug
- Configuration error

### 6.2 Real Historical Case

From the earlier audit, the `milvus_start` possibly-unbound bug:
- Inside `memory_manager.get_vector_search_results`, `milvus_start` was assigned only at line 729 (inside the try)
- The `except` block at line 782 referenced `milvus_start`
- If an exception fired before line 729 → `UnboundLocalError` was thrown inside the `except`
- **The original ES/Milvus error was masked**; what the logger saw was "variable referenced before assignment"

This is the classic side-effect of catch-all: a locatable failure becomes a second-order puzzle.

### 6.3 Observability Loss

71% of excepts being `except Exception` means:
- The Prometheus error-type metric (`record_retrieve_error`) can only report `'unknown_error'`
- Alerts can only fire on overall error rate, not on specific error categories
- Sentry / error-aggregation tools cannot get a specific exception class — every issue collapses into one bucket

---

## 7. Refactoring Guide

### 7.1 Decision Tree: do you need a try?

```
Can this line raise?
├── No → don't wrap it
├── Yes, raises a ProgrammingError (NameError/TypeError, etc.)
│        → don't wrap it; let CI + type-checking + crash-on-deploy surface it
├── Yes, raises a ResourceError (DBError/HTTPError/Timeout, etc.)
│        ├── I can recover → try only that line; except a specific type; carry on
│        ├── I can degrade → try only that line; except a specific type; emit a degradation metric
│        ├── I cannot recover → don't wrap it; let it propagate
│        └── It's a fire-and-forget background task → wrap the whole worker, but emit a failure metric
```

### 7.2 Replacement Cookbook

| Current pattern | Recommended replacement |
|---|---|
| `try: ...; except Exception as e: logger.error(e); raise` | `try: ...; except SpecificError as e: raise NewError(...) from e` |
| `try: api_call() except Exception: time.sleep(1); retry` | `@tenacity.retry(stop=stop_after_attempt(3), wait=wait_exponential())` |
| `try: ...; except Exception: pass` | Don't. Let it crash. If you really don't care: `contextlib.suppress(SpecificError)` |
| `except Exception: return None` (business layer) | Don't catch; let the controller turn it into 5xx |
| `traceback.print_exc()` | `logger.error("...", exc_info=True)` |
| `logger.error(f"...{var}")` | `logger.error("...%s", var)` |

### 7.3 Refactor Priority

Ordered by ROI, **Phase 1 is mechanical fixes that don't change behaviour**:

| Priority | Item | Volume | Approach | Risk |
|---|---|---|---|---|
| P0 | `traceback.print_exc()` → `exc_info=True` | 20 | Semi-automatic (grep + sed) | Very low |
| P0 | f-string in logger → `%`-style | 247 | ruff `G004` + manual review | Low |
| P1 | Split the top-5 oversize try blocks | 5 files | Manual | Medium |
| P1 | Convert biz_layer catch-all to upward propagation | ~15 sites | Manual + add boundary except in controllers | Medium-high |
| P2 | `except Exception` → specific exceptions | ~555 | Gradual; a few per PR | High |
| P3 | Replace hand-written retries with `tenacity` | Scattered | Rewrite | Medium |

### 7.4 Worked Refactor: `mem_memorize.memorize()`

**Current** (simplified):
```python
async def memorize(request) -> int:
    try:
        for memcell in memcells:
            await process_memory_extraction(memcell, ...)
        return memories_count
    except Exception as e:
        logger.error(f"failed: {e}")
        return 0
```

**After refactor**:
```python
async def memorize(request) -> int:
    # A single failing memcell should not block the others
    processed = 0
    failures = []
    for memcell in memcells:
        try:
            await process_memory_extraction(memcell, ...)
            processed += 1
        except (MilvusError, ESError, MongoError) as e:
            # Known storage-layer failure: record, emit metric, continue
            failures.append((memcell.event_id, e))
            record_memorize_error(stage='persist', error_type=type(e).__name__)
            logger.error("memcell %s failed: %s", memcell.event_id, e, exc_info=True)
            # Don't swallow LLMError, NameError, ValidationError — let them propagate

    if failures and len(failures) == len(memcells):
        # All failed → real outage, surface as 5xx
        raise MemorizeFailure(failures)
    return processed
```

Benefits:
- Partial failure becomes visible (metric + log)
- Total failure raises a 5xx, so the client knows
- Programming bugs (NameError, etc.) are no longer swallowed

---

## 8. Tooling to Prevent Re-Regression

### 8.1 Recommended ruff Rules

Add these to the `select` list in `[tool.ruff.lint]` of `pyproject.toml`:

```toml
select = [
    # ... existing rules ...
    "BLE",       # flake8-blind-except — catches bare `except:` and `except Exception:` without a logger
    "G",         # flake8-logging-format — catches f-strings inside logger calls
    "TRY",       # tryceratops — the full exception-handling rule family
]

# In Phase 1 enable per-directory to avoid 500+ violations exploding at once
[tool.ruff.lint.per-file-ignores]
"src/biz_layer/**/*.py" = ["BLE001", "TRY"]  # temporarily exempt this directory; tighten over time
```

**Key rules**:

| Rule | What it catches | Notes |
|---|---|---|
| `BLE001` | `except Exception` or bare `except` | Enabling this alone surfaces 555 violations |
| `G004` | f-string inside a logger call | 247 occurrences |
| `G201` | `logger.error(...)` that should pass `exc_info=True` | |
| `TRY003` | Raising `Exception(f"...{var}")` with an inline message | Encourages defining custom exception classes |
| `TRY200` | A `raise` inside `except` missing `from e` | Loses the cause chain |
| `TRY300` | `return` inside `try` that should move to `else` | Keeps the try scope narrow |
| `TRY400` | `logger.error` in an except where `logger.exception` is appropriate | |

### 8.2 Metrics to Add

During and after the refactor, add these metrics to observe progress:

- `except_handled_total{module, exception_type}` — counts the exceptions actually caught
- `silent_failure_total{module, function}` — incremented when an except returns a default value instead of raising
- `error_type_unknown_ratio` — the share of `'unknown_error'` labels in total errors (target < 5%)

### 8.3 Codified Conventions

Suggested additions to `docs/dev_docs/development_standards.md`:

> **Three iron rules of exception handling**
> 1. **The business layer (`biz_layer`) must not catch `Exception`.** Let exceptions propagate to the controller.
> 2. **Boundary code (`infra_layer`, external API calls in `agentic_layer`) must catch specific exception types** and use `raise ... from e` to preserve the cause chain.
> 3. **Never use `traceback.print_exc()` in an `except` block.** Always use `logger.exception(msg)` or `logger.error(msg, exc_info=True)`.

---

## 9. Action Plan (organised by sprint)

### Sprint 1 (1 day, zero risk)
- [ ] Replace all `traceback.print_exc()` with `exc_info=True` (20 sites)
- [ ] Enable `ruff G004` and run `--fix` to clean up f-string-in-logger (247 sites)
- [ ] Enable `ruff BLE001` and add it to `per-file-ignores` as the baseline
- [ ] Merge the two `RetryConfig` classes (§4.4) → single source

### Sprint 2 (2-3 days, medium risk)
- [ ] Split the top-5 oversize try blocks (one independent PR + test coverage each)
- [ ] Refactor `mem_memorize.memorize()` along the lines of §7.4
- [ ] Add boundary except in controllers to catch the specific exceptions thrown by the business layer
- [ ] Add sleep + jitter to Type B LLM output-format retries (§4.3), or rewrite them as "feedback retries"

### Sprint 3 (1 week, gradual)
- [ ] Introduce a custom exception hierarchy (`MemorizeError`, `RetrieveError`, `ExtractionError`, `InvalidLLMOutputError`)
- [ ] Boundary code (repositories, external API clients) switches `except Exception` to specific types
- [ ] Each PR shrinks the scope of ruff `BLE001` per-file-ignores
- [ ] Adopt `tenacity` and consolidate the 6 Type A HTTP retries (§4.2)

### Sprint 4 (long term)
- [ ] Replace the crude Type E fallback in `HybridVectorizeService` / `HybridRerankService` with `aiobreaker` (§4.5)
- [ ] Add `partial_failure_count` metrics to Type F loop-continue paths (§4.6)
- [ ] Global retry budget (caps on total time / total call count) — design + implementation
- [ ] Add exception handling to the PR review checklist
- [ ] Run mutation testing to verify that `except` block behaviour really has coverage

---

## Appendix A: Commands to Reproduce the Numbers in This Report

```bash
# Total try blocks
grep -rn "^\s*try:" src/ --include="*.py" | wc -l

# Total function definitions
grep -rn "^\s*\(async \)\?def " src/ --include="*.py" | wc -l

# Distribution of except clause shapes
grep -rh "^\s*except" src/ --include="*.py" \
  | sed -E 's/.*(except[^:]*).*/\1/' \
  | sort | uniq -c | sort -rn

# f-string in logger
grep -rn 'logger\.\(debug\|info\|warning\|error\|critical\)(f"' \
  src/ --include="*.py" | wc -l

# Remaining traceback.print_exc()
grep -rn "traceback.print_exc()" src/ --include="*.py" | wc -l
```

## Appendix B: References

- [PEP 657 — Include Fine-Grained Error Locations in Tracebacks](https://peps.python.org/pep-0657/)
- [What's New in Python 3.11: Zero-cost Exceptions](https://docs.python.org/3/whatsnew/3.11.html#cpython-bytecode-changes)
- [Ruff Tryceratops rules](https://docs.astral.sh/ruff/rules/#tryceratops-try)
- [tenacity documentation](https://tenacity.readthedocs.io/)
- Robert C. Martin, *Clean Code*, Ch. 7 "Error Handling"
