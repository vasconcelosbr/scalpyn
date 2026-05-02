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

## WebSocket scope note

The exchange adapters are REST-only (`rg websocket backend/app/exchange_adapters/`
returns no matches), so REST instrumentation in `gate_adapter._request`,
`gate_adapter._public_get` and `binance_adapter._request` covers 100 % of
exchange traffic and feeds the A4 error-rate denominator.

---

## Section 1 — Top stats (refresh: 30 s)

### 1.1 System Status (3-input composite OK / WARN / CRIT)

* **Datasource:** `-- Mixed --` (Prometheus + Postgres)
* **Panel type:** Stat with value mappings (0 → CRIT, 1 → WARN, 2 → OK)
* **Inputs (three real signals, no proxies):**

  ```promql
  # A — average robust-indicator confidence (Prometheus)
  avg(indicator_confidence)
  ```

  ```sql
  -- B — % NO_DATA across all indicator entries in the last 15 minutes
  SELECT 100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'NO_DATA')
       / NULLIF(COUNT(*),0) AS pct
  FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
  WHERE timestamp > NOW() - INTERVAL '15 minutes';
  ```

  ```promql
  # C — exchange request error rate over the last 5 minutes
  sum(rate(exchange_request_errors_total[5m]))
    / sum(rate(exchange_request_latency_seconds_count[5m]))
  ```

* **Combined via Grafana expressions** (`__expr__` reduce + math nodes):

  ```text
  D = reduce(A, last)
  E = reduce(B, last)
  F = reduce(C, last)
  G = 2 - min(($D < 0.6) + ($E >= 20) + ($F >= 0.05), 1)
        - min(($D < 0.45) + ($E >  25) + ($F >  0.0625), 1)
  ```

  G is the displayed field (the panel's `reduceOptions.fields = "/^G$/"`
  hides A–F).
* **OK / WARN / CRIT semantics — exactly per the task spec:**
  * **OK (G = 2):**  A > 0.6  AND  B < 20  AND  C < 0.05  (spec verbatim).
  * **WARN (G = 1):** at least one threshold is breached but **by ≤ 25 %** —
    A ∈ [0.45, 0.6)  **or**  B ∈ [20, 25]  **or**  C ∈ [0.05, 0.0625].
    (25 % of the OK limits: 0.6 → 0.45,  20 → 25,  0.05 → 0.0625.)
  * **CRIT (G = 0):** at least one threshold is breached by **> 25 %** —
    A < 0.45  **or**  B > 25  **or**  C > 0.0625.

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

**Single Postgres query, four numeric columns rendered as four gauges in
one panel** — exactly per the task spec. The Grafana `gauge` panel emits
one gauge per numeric field; per-column thresholds and display names are
attached via field-config overrides matched by column name.

```sql
SELECT
  100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'VALID')   / NULLIF(COUNT(*),0) AS pct_valid,
  100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'NO_DATA') / NULLIF(COUNT(*),0) AS pct_no_data,
  100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'STALE')   / NULLIF(COUNT(*),0) AS pct_stale,
  100.0 * COUNT(*) FILTER (WHERE value->>'status' = 'ERROR')   / NULLIF(COUNT(*),0) AS pct_error
FROM indicator_snapshots, LATERAL jsonb_each(indicators_json)
WHERE timestamp > NOW() - INTERVAL '15 minutes';
```

Per-column thresholds (applied via `fieldConfig.overrides` matched
`byName`):

| Column        | Display name | Green       | Yellow      | Red       |
|---------------|--------------|-------------|-------------|-----------|
| `pct_valid`   | `% VALID`    | ≥ 90        | 70–90       | < 70      |
| `pct_no_data` | `% NO_DATA`  | < 10        | 10–25       | ≥ 25      |
| `pct_stale`   | `% STALE`    | < 10        | 10–25       | ≥ 25      |
| `pct_error`   | `% ERROR`    | < 2         | 2–10        | ≥ 10      |

Embedded alert **A2** (`[Scalpyn] NO_DATA alto`) lives on this same
panel; its `data` block runs an independent SQL targeting only the
`pct_no_data` column so the threshold can be applied without depending
on the panel's display query layout.

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

Four Prometheus instant queries joined on the `exchange` label via
`merge` + `organize` transforms. Final columns match the task spec
verbatim: **p95 latency · error rate · last-update timestamp · status pill**.

```promql
# A — p95 latency by exchange (seconds)
histogram_quantile(0.95,
  sum by (le, exchange) (rate(exchange_request_latency_seconds_bucket[5m])))

# B — error rate by exchange (ratio 0–1)
sum by (exchange) (rate(exchange_request_errors_total[5m]))
  / sum by (exchange) (rate(exchange_request_latency_seconds_count[5m]))

# C — last-update timestamp (unix seconds; rendered as dateTimeAsLocal)
max by (exchange) (timestamp(exchange_request_latency_seconds_count))

# D — staleness in seconds, fed straight to the Status pill via range mappings
time() - max by (exchange) (timestamp(exchange_request_latency_seconds_count))
```

| Column         | Unit               | Green / OK    | Yellow / DEGRADED  | Red / DOWN     |
|----------------|--------------------|---------------|--------------------|----------------|
| `p95 latency`  | `s` (seconds)      | < 0.5         | 0.5–1.5            | ≥ 1.5          |
| `Error rate`   | `percentunit`      | < 5 %         | 5 %–10 %           | ≥ 10 %         |
| `Last update`  | `dateTimeAsLocal`  | n/a           | n/a                | n/a            |
| `Status` pill  | range-mapped (`D`) | `OK` (< 60 s) | `DEGRADED` (60–300 s) | `DOWN` (≥ 300 s) |

The Status column is rendered as a coloured background pill via Grafana's
`range` value mappings on column D — no extra Prometheus query is required.

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

## Section 8 — Embedded alerts (Grafana 10 unified alerting)

All four alerts are embedded **inside the dashboard JSON** using the
**Grafana 10 unified-alerting schema** (`alert.data` array of query +
expression nodes, `alert.condition` referencing the threshold node, plus
`labels`, `annotations`, and `noDataState` / `execErrState`). No legacy
`panel.alert.conditions` shape is used.

| # | Name                                  | Attached panel                       | Datasource | Condition                                                                                                            | For |
|---|---------------------------------------|--------------------------------------|------------|----------------------------------------------------------------------------------------------------------------------|-----|
| A1 | `[Scalpyn] Confidence baixo`         | 3 — `Confidence Média`               | Prometheus | `reduce(A, last) < 0.6`, where `A = avg(indicator_confidence)`                                                       | 5m  |
| A2 | `[Scalpyn] NO_DATA alto`             | 7 — `Data Quality (15m)` panel       | Postgres   | `reduce(A, last) > 25`, where A = NO_DATA SQL (15-minute window)                                                     | 5m  |
| A3 | `[Scalpyn] Rejection rate alto`      | 5 — `Rejection Rate (1h)`            | Postgres   | `reduce(A, last) > 50`, where A = section-1.5 SQL (1-hour window)                                                    | 5m  |
| A4 | `[Scalpyn] Exchange error rate alto` | 9 — `Exchanges` table                | Prometheus | `reduce(A, last) > 10`, where A = `100 * sum(rate(exchange_request_errors_total[5m])) / sum(rate(exchange_request_latency_seconds_count[5m]))` | 5m  |

Each `alert.data` array follows the same three-node pattern:

```text
[ A: query (Prom or SQL),  B: __expr__ reduce (last),  C: __expr__ threshold ]
```

with `condition: "C"`. Identical structure to `docs/grafana/alert-rules.yaml`.

* The companion provisioning file `docs/grafana/alert-rules.yaml` ships
  the same four rules with **matching UIDs** so the embedded blocks and
  the YAML stay in sync (importing both is idempotent — the YAML wins
  on conflict, which is the intended deploy path).
* Each rule carries `severity` (`critical` for A1/A2/A4, `warning`
  for A3) and `service: trading-engine` labels, plus a summary
  description and a `runbook_url` placeholder.
