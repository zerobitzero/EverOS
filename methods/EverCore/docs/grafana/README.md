# Grafana Dashboards

Dashboard JSON for the SLOs defined in
[`../dev_docs/slo_definitions.md`](../dev_docs/slo_definitions.md).

## Files

- `evercore_slo_overview.json` — one row per SLO (memorize success rate,
  retrieve p95 latency, LLM error rate, dependency availability).

## Import

1. Open Grafana → Dashboards → Import.
2. Paste the JSON, or upload the file.
3. Pick the Prometheus data source that scrapes EverCore's `/metrics`.

The default panels intentionally do not break down by `space_id` to keep
cardinality bounded; clone the dashboard and add per-tenant filters when
diagnosing a specific tenant.
