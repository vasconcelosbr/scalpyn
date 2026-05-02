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

---

## 8. Embedding the dashboard inside the Scalpyn frontend

The `/dashboard` page exposes a **Monitoring** tab that embeds this
Grafana dashboard inside an iframe so operators can see the metrics
without leaving the app. This section covers the one-time provisioning
needed to make that tab functional end-to-end.

### 8.1 Trust model — read this first

The Monitoring tab is wired to **anonymous viewer** access on Grafana.
Anyone who can reach the `NEXT_PUBLIC_GRAFANA_URL` host in their browser
can see the panels — same trust boundary as the rest of the trading UI
behind your Vercel auth. There is **no per-user SSO** on the iframe by
design: SSO would break the embed and was explicitly ruled out for this
task. Keep the Grafana hostname off public link aggregators if that is
not the trust posture you want.

### 8.2 Cloud SQL prep (one-time, run as superuser)

Grafana stores users, datasources, dashboards and alert state in its own
internal Postgres database. Without persistence it would be wiped on
every Cloud Run cold start.

```sql
CREATE DATABASE grafana_internal;
CREATE ROLE grafana_app LOGIN PASSWORD '<rotate-this-secret>';
GRANT ALL PRIVILEGES ON DATABASE grafana_internal TO grafana_app;
ALTER DATABASE grafana_internal OWNER TO grafana_app;
```

Connection budget impact: Grafana adds at most **4** Postgres datasource
connections (read-only `grafana_ro` to `scalpyn`) plus **~2** internal
DB connections (`grafana_app` to `grafana_internal`). The 22-connection
ceiling documented in `docs/db-pool-budget.md` therefore rises to ~28 —
stay on `db-g1-small` (50 max) or larger.

### 8.3 Secret Manager (one-time)

```bash
PROJECT=clickrate-477217

# Grafana admin login (used to manage users / datasources via UI).
openssl rand -hex 24 | tr -d '\n' | \
  gcloud secrets create grafana-admin-password --data-file=- --project $PROJECT

# Grafana internal DB password (matches the grafana_app role above).
echo -n "<password-from-CREATE-ROLE>" | \
  gcloud secrets create grafana-db-password --data-file=- --project $PROJECT

# Read-only datasource password (matches the grafana_ro role from §2).
echo -n "<grafana_ro-password>" | \
  gcloud secrets create grafana-postgres-ro-password --data-file=- --project $PROJECT

# Grant the deploy service account read access to all three.
for s in grafana-admin-password grafana-db-password grafana-postgres-ro-password; do
  gcloud secrets add-iam-policy-binding $s \
    --member=serviceAccount:scalpyn-service-account@$PROJECT.iam.gserviceaccount.com \
    --role=roles/secretmanager.secretAccessor --project $PROJECT
done
```

The Grafana service also reuses the existing `prometheus-bearer-token`
secret to scrape `/metrics` (see §1).

### 8.4 Cloud Run deploy

The image and deploy steps are codified in `grafana/cloudbuild-grafana.yaml`
(separate from the backend `cloudbuild.yaml` so a dashboard JSON change
does not trigger a backend redeploy and vice-versa). Wire a Cloud Build
trigger filtered to `grafana/**` and `docs/grafana/**`.

What that pipeline does:

1. Builds `grafana/Dockerfile` (a thin wrapper on `grafana/grafana-oss:11`)
   which COPYs in the dashboard JSON and the two provisioning files
   (`grafana/provisioning/datasources/datasources.yaml` and
   `grafana/provisioning/dashboards/dashboards.yaml`) so the dashboard
   appears on first cold start with no manual import.
2. Pushes the image to Artifact Registry under the same `scalpyn` repo.
3. Deploys it as Cloud Run service `scalpyn-grafana` with:
   - `--ingress all` (the iframe is loaded from the user's browser, so
     Grafana itself must be public; only the datasources stay private).
   - `--add-cloudsql-instances clickrate-477217:us-central1:scalpyn` so
     Grafana can reach the Cloud SQL instance over the unix socket.
   - `--vpc-connector` + `--vpc-egress=private-ranges-only` so it can
     hit Prometheus on a private IP without exposing it publicly.
   - The full set of `GF_*` env vars that bake in iframe-embedding
     requirements (anonymous viewer, `GF_SECURITY_ALLOW_EMBEDDING=true`,
     `cookie_samesite=none`, `cookie_secure=true`, dark theme, etc.).
   - The four secret-backed env vars (admin password, internal DB
     password, datasource password, Prometheus bearer token).

Substitution variables you'll want to set on the trigger:

| Substitution            | Default                                 | Purpose                                            |
|-------------------------|-----------------------------------------|----------------------------------------------------|
| `_VPC_CONNECTOR`        | `scalpyn-connector`                     | VPC connector name (must match the backend's)      |
| `_PROMETHEUS_URL`       | `http://10.0.0.10:9090`                 | Private IP of the Prometheus scrape target         |
| `_CLOUD_SQL_PRIVATE_IP` | `10.0.0.20`                             | Cloud SQL **private** IP for the read-only Postgres datasource (find via `gcloud sql instances describe scalpyn`) |
| `_RUNTIME_SA`           | `scalpyn-service-account@clickrate-477217.iam.gserviceaccount.com` | Runtime service account passed via `--service-account`; must hold `secretmanager.secretAccessor` on the four secrets in §8.3 |
| `_GRAFANA_PUBLIC_URL`   | `https://grafana.scalpyn.app`           | The hostname users will hit; sets `GF_SERVER_ROOT_URL` |

**Datasource transport, why two paths:** Grafana's *internal* DB
(`grafana_internal`) reaches Cloud SQL via the Unix socket
(`/cloudsql/...`) — Grafana's backend speaks Postgres natively over the
socket and SSL is meaningless on a local socket, so
`GF_DATABASE_SSL_MODE=disable` is correct. The *user-facing* Postgres
**datasource**, however, cannot use a socket path with
`sslmode=require` — Grafana's datasource layer errors out on save. We
therefore route the datasource over the Cloud SQL **private IP** (TCP,
port 5432, `sslmode=require`), which the VPC connector grants Grafana
access to without going over the public internet. After the first
deploy, run **Connections → Data sources → PostgreSQL → Save & test**
in the Grafana UI to confirm the datasource returns "Database
Connection OK"; if it errors, double-check `_CLOUD_SQL_PRIVATE_IP` is
correct and that the VPC connector can reach it (most common cause:
`--vpc-egress=all-traffic` is needed instead of `private-ranges-only`
when the IP falls outside RFC 1918 ranges, but Cloud SQL private IPs
are always RFC 1918, so the default works).

### 8.5 Anonymous-viewer permission on the dashboard

Pick **one** of the two options the spec calls out:

* **Option A — recommended.** Grant `Viewer` on the dashboard to the
  `Anonymous` org. After the first deploy, log in to Grafana as `admin`
  (using `grafana-admin-password`) and on the **Scalpyn Trading Engine**
  dashboard go to **Dashboard settings → Permissions → Add a permission
  → Role: Viewer → Permission: View → Apply**. This keeps the dashboard
  inside the regular org and is revocable per-dashboard.
* **Option B.** Use Grafana's built-in **Public dashboards** feature
  (Dashboard settings → Public dashboard → Enable). Grafana mints a
  share token; use the `…/public-dashboards/<token>` URL as the iframe
  source instead of the `/d/scalpyn-trading-engine` path. The trade-off
  is that the share token is not revocable per-user — the entire URL
  must be rotated to revoke access.

### 8.6 Custom domain (optional but recommended)

Map `grafana.scalpyn.app` (or whichever apex you use) to the
`scalpyn-grafana` Cloud Run service via:

```bash
gcloud beta run domain-mappings create \
  --service scalpyn-grafana \
  --domain grafana.scalpyn.app \
  --region us-central1 --project clickrate-477217
```

…or place it behind the existing Cloud Load Balancer with a serverless
NEG. If you skip this step, the iframe will fall back to the raw
`https://scalpyn-grafana-<hash>-uc.a.run.app` URL — works, but uglier
and less stable across redeploys.

### 8.7 Frontend env var

The Monitoring tab reads `NEXT_PUBLIC_GRAFANA_URL` at build time. Set it
in your hosting provider's project settings:

```
NEXT_PUBLIC_GRAFANA_URL=https://grafana.scalpyn.app
```

(or the raw `*.run.app` URL if you skipped §8.6). Must NOT include a
trailing slash or path — the frontend appends
`/d/scalpyn-trading-engine?...` itself. When the variable is missing the
tab renders a clean empty state pointing back at this README, so the
rest of `/dashboard` keeps working.

After updating the variable, **trigger a frontend redeploy** —
`NEXT_PUBLIC_*` is inlined at build time, so a fresh build is required
for the iframe to pick up the new value.

### 8.8 Smoke test

1. Open `<frontend>/dashboard` and confirm two tabs are visible.
2. Click **Monitoring**. Within 5 s the Grafana dashboard should render
   inside the iframe with no Grafana login prompt and no
   `X-Frame-Options` errors in the browser console.
3. Click into any panel — Grafana's panel-detail view should still load
   inside the same iframe (kiosk mode preserves drilldown).
4. Switch back to **Overview** — the existing dashboard renders
   identically to before the embed change.
5. Test the empty state: temporarily unset `NEXT_PUBLIC_GRAFANA_URL` and
   redeploy — the Monitoring tab should show "Monitoring is not
   configured yet" without crashing the page.
