# Scalpyn Trading Engine — Dashboard Query Reference

Companion to `scalpyn-trading-engine.json`. Every panel below maps to a real
Prometheus metric or PostgreSQL column — there are no fictitious series.

> **Architectural rule (all PromQL):** Cloud Run runs `WEB_CONCURRENCY=2`
> uvicorn workers, so each counter / histogram is exposed as a per-process
> series. **Every PromQL query must `sum by (…)`** to aggregate across
> workers — never use raw `rate()` on a single series. The queries below
> already follow this rule.
>
> **Architectural rule (all SQL on `score`):** `indicator_snapshots.score` is
> `NUMERIC(10,2) NULL`. Every aggregation on it uses `WHERE score IS NOT
> NULL` and `NULLIF(..., 0)` on denominators to avoid Grafana "no data"
> panels when scores have not been computed yet.

---

## Section 1 — Top stats (refresh: 30 s)

### 1.1 System Status (composite OK / WARN / CRIT)

* **Datasource:** `${prometheus}`
* **Panel type:** Stat with value mappings (0 → CRIT, 1 → WARN, 2 → OK)
* **Query:**

  ```promql
  clamp_max(
    ((avg(indicator_confidence) > bool 0.6))
    + ((sum(rate(exchange_request_errors_total[5m]))
        / sum(rate(exchange_request_latency_seconds_count[5m])) < bool 0.05)
       or on() vector(1)),
    2)
  ```

  * Each `> bool` / `< bool` comparison returns 0 or 1.
  * `or on() vector(1)` ensures the error-rate term collapses to "OK" when no
    exchange traffic has occurred yet (prevents false CRITs at cold start).
  * Result range: 0 (both bad) · 1 (one bad) · 2 (both healthy).
* **Thresholds:** absolute, mapped — `red` (default), no further steps; the
  three colours come from value mappings.
* **NO_DATA dimension:** monitored separately by panel 2 — keeping it out of
  the composite avoids a 3-input Prom expression that would require an
  expressions-engine math node to mix with the SQL gauge.

### 1.2 Score médio (1h)

* **Datasource:** `${postgres}`
* **Panel type:** Stat (last value)
* **Query:**

  ```sql
  SELECT AVG(score) AS "Score médio"
  FROM indicator_snapshots
  WHERE timestamp > NOW() - INTERVAL '1 hour'
    AND score IS NOT NULL;
  ```
* **Thresholds:** red < 40 · orange 40–60 · green ≥ 60
* **Note:** `score` is **not** a Prometheus metric. It lives only in
  `indicator_snapshots.score`.

### 1.3 Confidence média

* **Datasource:** `${prometheus}`
* **Query:** `avg(indicator_confidence)`
* **Range:** 0–1 · **Thresholds:** red < 0.5 · orange 0.5–0.6 · green ≥ 0.6

### 1.4 Data Quality % (VALID, 1h)

* **Datasource:** `${postgres}`
* **Query:**

  ```sql
  SELECT 100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'VALID')
       / NULLIF(COUNT(*),0) AS pct_valid
  FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
  WHERE timestamp > NOW() - INTERVAL '1 hour';
  ```
* **Unit:** `percent` · **Thresholds:** red < 70 · orange 70–90 · green ≥ 90

### 1.5 Rejection Rate (1h)

* **Datasource:** `${postgres}`
* **Query:**

  ```sql
  SELECT 100.0 * COUNT(*) FILTER (WHERE decision = 'REJECT')
       / NULLIF(COUNT(*),0) AS pct_reject
  FROM decisions_log
  WHERE created_at > NOW() - INTERVAL '1 hour';
  ```
* **Unit:** `percent` · **Thresholds:** green < 30 · orange 30–50 · red ≥ 50
* **Note:** `total_trades` is **not** a Prometheus counter — derived from
  `decisions_log` row counts.

### 1.6 Trades 1h (approved vs rejected, bar gauge)

* **Datasource:** `${postgres}`
* **Query:**

  ```sql
  SELECT decision AS metric, COUNT(*) AS value
  FROM decisions_log
  WHERE created_at > NOW() - INTERVAL '1 hour'
  GROUP BY decision
  ORDER BY decision;
  ```

---

## Section 2 — Data-Quality Gauges (15 m, refresh: 30 s)

Four independent Postgres gauges, one query per panel (Grafana renders them
faster as separate single-cell queries than as a 4-column transform).

```sql
-- % VALID
SELECT 100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'VALID')
     / NULLIF(COUNT(*),0) AS pct
FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
WHERE timestamp > NOW() - INTERVAL '15 minutes';

-- % NO_DATA
SELECT 100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'NO_DATA')
     / NULLIF(COUNT(*),0) AS pct
FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
WHERE timestamp > NOW() - INTERVAL '15 minutes';

-- % STALE
SELECT 100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'STALE')
     / NULLIF(COUNT(*),0) AS pct
FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
WHERE timestamp > NOW() - INTERVAL '15 minutes';

-- % ERROR
SELECT 100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'ERROR')
     / NULLIF(COUNT(*),0) AS pct
FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
WHERE timestamp > NOW() - INTERVAL '15 minutes';
```

| Gauge      | Green       | Yellow      | Red       |
|------------|-------------|-------------|-----------|
| `% VALID`  | ≥ 90        | 70–90       | < 70      |
| `% NO_DATA`| < 10        | 10–25       | ≥ 25      |
| `% STALE`  | < 10        | 10–25       | ≥ 25      |
| `% ERROR`  | < 2         | 2–10        | ≥ 10      |

> The single-query, Grafana-transform variant from the original spec is also
> valid and returns identical numbers. The exploded form here trades a tiny
> amount of duplicated SQL for one less DB round-trip per panel render and
> avoids the `Reduce → Organize` pipeline entirely, which is the most common
> place where Postgres-via-Grafana panels silently break on schema upgrades.

---

## Section 3 — Confidence over time (refresh: 30 s)

* **Datasource:** `${prometheus}`
* **Panel type:** Time series (line, smooth, fill 8 %)
* **Query:**

  ```promql
  avg by (symbol) (avg_over_time(indicator_confidence[5m]))
  ```
* **Legend:** `{{symbol}}`
* **Y-axis:** 0–1 · **Reference line:** `0.60` (red below)

---

## Section 4 — Exchange Status table (refresh: 30 s)

Three Prometheus instant queries joined on the `exchange` label via
`merge` + `organize` transforms.

```promql
# A — p95 latency by exchange (seconds)
histogram_quantile(0.95,
  sum by (le, exchange) (rate(exchange_request_latency_seconds_bucket[5m])))

# B — error rate by exchange (ratio 0–1)
sum by (exchange) (rate(exchange_request_errors_total[5m]))
  / sum by (exchange) (rate(exchange_request_latency_seconds_count[5m]))

# C — staleness: seconds since last sample
time() - max by (exchange) (timestamp(exchange_request_latency_seconds_count))
```

| Column        | Unit          | Green     | Yellow      | Red       |
|---------------|---------------|-----------|-------------|-----------|
| `p95 latency` | `s` (seconds) | < 0.5     | 0.5–1.5     | ≥ 1.5     |
| `Error rate`  | `percentunit` | < 5 %     | 5 %–10 %    | ≥ 10 %    |
| `Staleness`   | `s` (seconds) | < 60      | 60–300      | ≥ 300     |

---

## Section 5 — Score per symbol (refresh: 1 m)

* **Datasource:** `${postgres}`
* **Panel type:** Time series
* **Query:**

  ```sql
  SELECT timestamp AS time, symbol AS metric, score AS value
  FROM indicator_snapshots
  WHERE $__timeFilter(timestamp) AND score IS NOT NULL
  ORDER BY 1;
  ```
* **Notes:** `$__timeFilter(timestamp)` is the standard Grafana macro that
  binds to the dashboard time picker. `score IS NOT NULL` is mandatory —
  see the architectural rule at the top of this document.

---

## Section 6 — Critical Indicators table (5 m, refresh: 30 s)

* **Datasource:** `${postgres}`
* **Panel type:** Table (200-row hard cap)
* **Query:**

  ```sql
  SELECT
    s.symbol,
    ind.key AS indicator,
    ind.value->>'timeframe' AS timeframe,
    ind.value->>'value'      AS value,
    ind.value->>'status'     AS status,
    ind.value->>'source'     AS source,
    (ind.value->>'confidence')::numeric AS confidence
  FROM indicator_snapshots s,
       LATERAL jsonb_each(s.indicators_json) AS ind
  WHERE s.timestamp > NOW() - INTERVAL '5 minutes'
    AND ind.value->>'status' IN ('NO_DATA', 'STALE', 'ERROR')
  ORDER BY s.timestamp DESC, s.symbol, indicator
  LIMIT 200;
  ```
* **Cell colouring:**
  * `status` → background colour mapped (NO_DATA = red · STALE = orange · ERROR = red)
  * `confidence` → text colour, red < 0.5 · orange 0.5–0.7 · green ≥ 0.7

---

## Section 7 — Rejection donut + companion stats (refresh: 30 s)

```promql
# Donut — rejections per second by reason (5m window)
sum by (reason) (rate(score_rejection_total[5m]))

# Total rejections in the last 5 minutes
sum(rate(score_rejection_total[5m])) * 300

# Top reason in the last 5 minutes
topk(1, sum by (reason) (rate(score_rejection_total[5m])))
```

> The `reason` label is whatever `backend/app/tasks/pipeline_scan.py` passes
> to `increment_rejection(reason_key)` — currently the colon-prefix of
> `result.rejection_reason`. The dashboard does **not** hard-code an enum, so
> any new rejection reason added to the engine appears automatically.

---

## Section 8 — Pipeline performance (refresh: 30 s)

```promql
# p50 per indicator
histogram_quantile(0.50,
  sum by (le, indicator) (rate(indicator_computation_duration_seconds_bucket[5m])))

# p95 per indicator
histogram_quantile(0.95,
  sum by (le, indicator) (rate(indicator_computation_duration_seconds_bucket[5m])))
```

* **Unit:** `s` (seconds) · **Legend:** `p50 · {{indicator}}`, `p95 · {{indicator}}`

---

## Embedded alerts (Grafana 10 unified alerting)

| # | Name                              | Datasource | Condition                                                                                               | For |
|---|-----------------------------------|------------|---------------------------------------------------------------------------------------------------------|-----|
| A1 | `[Scalpyn] Confidence baixo`     | Prometheus | `avg(indicator_confidence) < 0.6`                                                                       | 5m  |
| A2 | `[Scalpyn] NO_DATA alto`         | Postgres   | result of the `% NO_DATA` query in section 2 > 25                                                       | 5m  |
| A3 | `[Scalpyn] Rejection rate alto`  | Postgres   | result of section 1.5 > 50                                                                              | 5m  |
| A4 | `[Scalpyn] Exchange error rate alto` | Prometheus | `100 * sum(rate(exchange_request_errors_total[5m])) / sum(rate(exchange_request_latency_seconds_count[5m])) > 10` | 5m  |

* **Embedded in the dashboard JSON:** A1 is attached to panel 3
  (`Confidence Média`); A4 is attached to panel 12 (`Exchanges` table).
  Grafana 10 imports legacy `panel.alert` blocks and converts them to the
  unified-alerting model on first save.
* **Provisioned separately:** A2 and A3 are SQL-based and ship in
  `alert-rules.yaml` because Grafana's panel-attached legacy alerts cannot
  reduce a SQL `table` result without a math expression node.

Each rule carries `severity` (`critical`) and `service: trading-engine`
labels plus a summary annotation pointing operators to the relevant adapter
or pipeline file.
