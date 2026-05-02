# Grafana — Scalpyn Trading Engine Monitoring

Production-grade Grafana 10 dashboard for the Scalpyn trading engine.
This directory ships:

| File | Purpose |
|------|---------|
| `scalpyn-trading-engine.json` | Importable dashboard model — **8 panel-group rows** (sections 1–7 visual + section 8 collapsed alerts reference), 14 visual panels, **all four alerts (A1–A4) embedded** in the Grafana 10 unified-alerting schema. |
| `queries.md` | One section per panel: title, datasource, full PromQL/SQL, refresh interval, thresholds. |
| `alert-rules.yaml` | Grafana provisioning file with the four unified-alerting rules (A1–A4) for repeatable deploys. |
| `README.md` | This file — setup, datasource creation, import, and operational caveats. |

---

## 1. Prometheus scrape configuration

The backend exposes Prometheus metrics at **`GET /metrics`** (mounted in
`backend/app/api/metrics.py`). Six series are scraped:

| Metric | Type | Labels |
|--------|------|--------|
| `indicator_computation_duration_seconds` | Histogram | `symbol`, `indicator`, `source` |
| `indicator_confidence` | Gauge | `symbol` |
| `indicator_staleness_seconds` | Gauge | `symbol`, `indicator` |
| `score_rejection_total` | Counter | `reason` |
| `exchange_request_latency_seconds` | Histogram | `exchange` (`gate` \| `binance`) |
| `exchange_request_errors_total` | Counter | `exchange`, `kind` (`http` \| `transport`) |

Sample `prometheus.yml` job (Cloud Run target):

```yaml
scrape_configs:
  - job_name: 'scalpyn-backend'
    metrics_path: /metrics
    scheme: https
    scrape_interval: 30s
    scrape_timeout: 10s
    # Required: /metrics is gated by a shared bearer token (see below).
    bearer_token_file: /etc/prometheus/scalpyn-metrics-token
    static_configs:
      - targets: ['scalpyn-backend-<hash>-uc.a.run.app']
```

> ### `/metrics` access control (Task #167)
>
> Two layers, both required:
>
> **1. Network perimeter (primary).** The Cloud Run service is deployed
> with `--ingress=internal-and-cloud-load-balancing` (see
> `cloudbuild.yaml`). The raw `*.run.app` hostname is unreachable from
> the public internet — `curl` against it returns `403 Forbidden` from
> Google's front end *before* a request ever hits the application.
> Frontend traffic continues to flow through the Vercel proxy
> (`frontend/app/api/[...path]/route.ts`) by pointing `BACKEND_URL` at
> the Cloud Load Balancer hostname (or a custom domain mapped to the
> LB), and Prometheus scrapes from inside the same VPC connector or
> through an LB allow-list — see the operator setup below.
>
> **2. Application-level bearer token (defense in depth).** Even if the
> ingress perimeter is ever loosened, `backend/app/api/metrics.py`
> requires an `Authorization: Bearer <token>` header that matches the
> `PROMETHEUS_BEARER_TOKEN` env var (mounted from the
> `prometheus-bearer-token` Secret Manager secret). When the env var is
> unset the endpoint returns `404 Not Found`; when set, requests without
> the matching token get `401 Unauthorized`. Prometheus supports this
> natively via `bearer_token_file` (already wired in the scrape config
> above), so no sidecar is needed.
>
> | Reachability                                                  | Result |
> |---------------------------------------------------------------|--------|
> | Public internet → `https://scalpyn-backend-…run.app/metrics`  | `403` (Cloud Run ingress blocks) |
> | Internal/LB caller, no `Authorization` header                 | `401` + `WWW-Authenticate: Bearer` |
> | Internal/LB caller with correct `Bearer` token                | `200` + Prometheus exposition body |
>
> #### One-time operator setup
>
> ```bash
> # ── A. Provision the Cloud Load Balancer in front of Cloud Run ─────────────
> # Skipped here for brevity — follow the standard "Serverless NEG → backend
> # service → URL map → HTTPS proxy → forwarding rule" recipe. The result is
> # a stable LB IP/hostname (e.g. `api.scalpyn.app`) that fronts the Cloud
> # Run service. After this, point Vercel's BACKEND_URL env var at the LB
> # hostname so the frontend keeps reaching the backend.
>
> # ── B. Lock the bearer-token secret ───────────────────────────────────────
> openssl rand -hex 32 | tr -d '\n' | \
>   gcloud secrets create prometheus-bearer-token \
>     --data-file=- --project clickrate-477217
>
> gcloud secrets add-iam-policy-binding prometheus-bearer-token \
>   --member=serviceAccount:scalpyn-service-account@clickrate-477217.iam.gserviceaccount.com \
>   --role=roles/secretmanager.secretAccessor \
>   --project clickrate-477217
>
> # ── C. Re-deploy via Cloud Build ──────────────────────────────────────────
> # cloudbuild.yaml already passes both `--ingress=internal-and-cloud-load-
> # balancing` and `--update-secrets PROMETHEUS_BEARER_TOKEN=
> # prometheus-bearer-token:latest`, so a fresh build applies both at once.
>
> # ── D. Drop the same token on the Prometheus host ─────────────────────────
> gcloud secrets versions access latest --secret=prometheus-bearer-token \
>   --project clickrate-477217 \
>   | sudo tee /etc/prometheus/scalpyn-metrics-token > /dev/null
> sudo chmod 600 /etc/prometheus/scalpyn-metrics-token
> sudo chown prometheus:prometheus /etc/prometheus/scalpyn-metrics-token
> sudo systemctl reload prometheus
> ```
>
> #### Smoke test (post-deploy)
>
> ```bash
> # 1. Public Cloud Run URL → must be 403 (ingress blocks before the app)
> curl -s -o /dev/null -w "%{http_code}\n" \
>   https://scalpyn-backend-<hash>-uc.a.run.app/metrics
> # → 403
>
> # 2. LB hostname without the token → must be 401 (bearer gate)
> curl -s -o /dev/null -w "%{http_code}\n" \
>   https://api.scalpyn.app/metrics
> # → 401
>
> # 3. LB hostname with the token → must be 200 + text/plain Prometheus body
> curl -s -o /dev/null -w "%{http_code}\n" \
>   -H "Authorization: Bearer $(cat /etc/prometheus/scalpyn-metrics-token)" \
>   https://api.scalpyn.app/metrics
> # → 200
> ```
>
> **Rotation:** add a new secret version
> (`gcloud secrets versions add prometheus-bearer-token --data-file=…`),
> redeploy Cloud Run so the new value is picked up, then update
> `/etc/prometheus/scalpyn-metrics-token` and reload Prometheus. The
> ingress restriction stays in force throughout the rotation.

### Cloud Run multi-worker note

Cloud Run runs the backend with `WEB_CONCURRENCY=2` uvicorn workers (see
`backend/Dockerfile`). Prometheus therefore sees **two separate
`process_*` series and two copies of every counter/histogram per scrape**.
All PromQL in `queries.md` aggregates with `sum by (…)` for that reason.
Never paste a raw `rate(metric_name[5m])` query without an aggregator —
the result will only reflect a single worker.

---

## 2. PostgreSQL read-only role for Grafana

The dashboard touches exactly two tables — `indicator_snapshots` and
`decisions_log`. Grant the minimum scope:

```sql
-- run as superuser on the production DB
CREATE ROLE grafana_ro LOGIN PASSWORD '<rotate-this-secret>';
GRANT CONNECT ON DATABASE scalpyn TO grafana_ro;
GRANT USAGE  ON SCHEMA public   TO grafana_ro;

GRANT SELECT ON public.indicator_snapshots TO grafana_ro;
GRANT SELECT ON public.decisions_log       TO grafana_ro;

-- prevent accidental future grants from leaking via default privileges
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM grafana_ro;
```

> ### ⚠ Cloud SQL connection budget
>
> `docs/db-pool-budget.md` documents a **22-connection ceiling** today
> (2 uvicorn workers × (5 + 5) pool slots + 1 Celery + 1 beat). Each
> Grafana datasource pool adds at least 1 connection on every panel
> render that runs concurrently.
>
> | Cloud SQL tier | `max_connections` | Headroom after Grafana (1–4 conns) |
> |----------------|-------------------|------------------------------------|
> | `db-f1-micro`  | 25                | 0–2 (**not recommended**)          |
> | `db-g1-small`  | 50                | 24–27 (safe)                       |
> | `db-n1-standard-1` | 100           | 74–77 (comfortable)                |
>
> Stay on `db-g1-small` or larger; on `db-f1-micro` the headroom is gone
> the moment a second admin session opens. Configure the Grafana
> datasource with **Max open = 4**, **Max idle = 1**, **Max lifetime = 1h**.

---

## 3. Datasource setup (Grafana UI)

### Prometheus
1. **Connections → Data sources → Add → Prometheus**.
2. URL: `http://<your-prometheus>:9090` (or the Grafana Cloud Prometheus URL).
3. Scrape interval: `30s` (matches `prometheus.yml`).
4. Save & test → must show "Data source is working".

### PostgreSQL
1. **Connections → Data sources → Add → PostgreSQL**.
2. Host: `<cloud-sql-private-ip>:5432`.
3. Database: `scalpyn`.
4. User: `grafana_ro` · Password: the one you set in the `CREATE ROLE` above.
5. **TLS/SSL Mode:** `require` (or `verify-ca` if you mounted the Cloud SQL CA).
6. **Connection limits:** Max open `4` · Max idle `1` · Max lifetime `1h`.
7. **PostgreSQL version:** match the running server (15 / 16 / 17).
8. **TimescaleDB:** enable the toggle if your prod DB has the extension —
   the dashboard does not require it, but enabling it lets future panels
   use `time_bucket()`.
9. Save & test.

---

## 4. Importing the dashboard

1. **Dashboards → Import**.
2. Click **Upload JSON file** and choose `scalpyn-trading-engine.json`.
3. The dashboard exposes two **datasource template variables** —
   `${prometheus}` and `${postgres}` — that are bound on import:
   * `prometheus` → pick the Prometheus datasource from step 3.
   * `postgres`   → pick the PostgreSQL datasource from step 3.

   Both variables also stay editable from the dashboard's variable
   selector, so the same JSON can be re-pointed at a staging Prometheus
   or read-replica Postgres without re-importing.
4. **Import**. The dashboard opens at UID `scalpyn-trading-engine`,
   default time range `now-1h → now`, refresh `30s`.
5. Optional: pin it to your Trading folder and star it.

The dashboard ships **all four alert rules embedded** inside the JSON
using the **Grafana 10 unified-alerting schema** (each `alert` block
carries a `data` array of query + `__expr__` reduce + threshold nodes
and a `condition` field — identical structure to the YAML provisioning
file in section 5):

| # | Title                                 | Attached panel                         |
|---|---------------------------------------|----------------------------------------|
| A1 | `[Scalpyn] Confidence baixo (<0.6)`  | 3 — Confidence Média                   |
| A2 | `[Scalpyn] NO_DATA alto (>25%)`      | 7 — Data Quality (15m) — 4-gauge panel |
| A3 | `[Scalpyn] Rejection rate alto (>50%)` | 5 — Rejection Rate (1h)              |
| A4 | `[Scalpyn] Exchange error rate alto (>10%)` | 9 — Exchanges table              |

Grafana 10 imports them straight into the `Scalpyn` rule group with no
legacy-to-unified migration step.

---

## 5. Provisioning the four alerts repeatably

The dashboard JSON already embeds all four alerts in Grafana 10
unified-alerting schema (see step 4 above), so importing the dashboard
is enough for one-off setups. For repeatable production deploys where
the dashboard might be re-imported by automation, also drop the
companion provisioning file in place — the rule UIDs match, so the
embedded blocks and the YAML stay in sync without duplication.

### 5.1 Resolve the datasource placeholders

`alert-rules.yaml` ships with two literal placeholders:

```yaml
datasourceUid: ${DS_PROMETHEUS}    # for A1 (confidence) and A4 (exchange error rate)
datasourceUid: ${DS_POSTGRES}      # for A2 (NO_DATA) and A3 (rejection rate)
```

Grafana provisioning does **not** auto-substitute these; you have to
replace them with the actual datasource UIDs from your Grafana
instance before dropping the file in
`/etc/grafana/provisioning/alerting/`. Two practical options:

* **Manual / IaC**: in **Connections → Data sources** click each
  datasource and copy its `uid` from the URL (e.g.
  `prometheus-prod-abc123`). Render the file via your IaC tool of
  choice — `envsubst`, Helm values, Ansible templates, Terraform
  `templatefile`, etc. Example:

  ```bash
  export DS_PROMETHEUS=prometheus-prod-abc123
  export DS_POSTGRES=postgres-prod-def456
  envsubst < docs/grafana/alert-rules.yaml \
    > /etc/grafana/provisioning/alerting/scalpyn.yaml
  sudo systemctl restart grafana-server
  ```

* **Pinned UIDs**: alternatively, declare the datasources themselves
  via Grafana provisioning (`/etc/grafana/provisioning/datasources/`)
  with hard-coded `uid: prometheus-prod` / `uid: postgres-prod` values
  and replace the placeholders with those literal UIDs once. Keeps
  every environment identical.

### 5.2 Drop in place and reload

```bash
cp /tmp/scalpyn.yaml /etc/grafana/provisioning/alerting/scalpyn.yaml
sudo systemctl restart grafana-server
```

The four rules will appear under the **Scalpyn** rule group. Edit the
contact-point name (`scalpyn-oncall`) at the top of `alert-rules.yaml`
to match the receiver you have configured (Slack, PagerDuty,
Opsgenie, etc.).

---

## 6. Smoke test after import

```bash
# 1. Prometheus side — every metric should appear
curl -s "https://<prom>/api/v1/label/__name__/values" \
  | jq -r '.data[]' | grep -E '^(indicator_|score_rejection|exchange_request)'

# Expected six metric families:
#   exchange_request_errors_total
#   exchange_request_latency_seconds
#   indicator_computation_duration_seconds
#   indicator_confidence
#   indicator_staleness_seconds
#   score_rejection_total

# 2. SQL side — every query in queries.md must return rows
psql "host=... user=grafana_ro dbname=scalpyn sslmode=require" \
  -c "SELECT COUNT(*) FROM indicator_snapshots WHERE timestamp > NOW() - INTERVAL '1 hour';"
psql "host=... user=grafana_ro dbname=scalpyn sslmode=require" \
  -c "SELECT COUNT(*) FROM decisions_log WHERE created_at > NOW() - INTERVAL '1 hour';"

# 3. Open the dashboard — every panel should render within 5s and the
#    "No data" badge must not appear on Score Médio, Confidence Média, or
#    the Exchanges table once the engine has run for a few minutes.

# 4. Alert binding check — open Alerting → Alert rules and confirm the
#    four "[Scalpyn] …" rules show state "Normal" (not "Error"). An
#    "Error" state usually means the embedded ${prometheus}/${postgres}
#    template variables didn't resolve to concrete datasource UIDs at
#    import time. Fix by either (a) re-importing after binding the
#    variables in the import wizard, or (b) provisioning the rules via
#    alert-rules.yaml (see §5) which uses literal datasource UIDs.
```

If a panel shows `No data`:
1. Check the datasource selector at the top of the dashboard — the two
   `${prometheus}` and `${postgres}` variables must point to the right
   datasources.
2. Check `queries.md` for the panel's exact query and run it directly in
   the **Explore** tab.
3. For the Postgres SQL stats with `score`: confirm `score IS NOT NULL`
   rows exist in the chosen time window — the column is nullable and
   blank during early pipeline runs.

---

## 7. Operational caveats

* **No fictitious metrics.** Every panel is wired to a series or column
  that exists today. The two metrics added in this task
  (`exchange_request_latency_seconds`, `exchange_request_errors_total`) are
  emitted from `backend/app/exchange_adapters/{binance_adapter.py,gate_adapter.py}`
  via the `_request` and `_public_get` chokepoints.
* **Dark theme** is enforced via `style: dark` in the dashboard model.
* **Time range** defaults to `now-1h`. The `Confidence ao longo do tempo`,
  `Score por símbolo`, and `Pipeline performance` panels rescale with the
  picker; the SQL stat panels in section 1 use fixed `INTERVAL '1 hour'`
  on purpose so they always reflect the same operator-meaningful window.
* **No frontend / mobile telemetry, log aggregation, or backfilled
  history** — those are explicitly out of scope (see `task-166.md`).
