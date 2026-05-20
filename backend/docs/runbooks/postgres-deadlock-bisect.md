# Postgres Deadlock Bisect (Task #273)

How to find which statement / which table / which Cloud Run revision
caused a `deadlock detected` (SQLSTATE `40P01`) burst in production,
and how to confirm the fix shipped in Task #273 (post-#251) is holding.

This runbook is the bisect procedure that the operator should follow
the moment Cloud SQL logs show a 40P01 spike — the root-cause class is
"two backends iterating the same hot row set in different orders" and
the fix is always the same shape (`sorted()` before per-row UPSERT),
but pinpointing the specific callsite needs the steps below.

---

## 0. Confirm the alert is real

```bash
gcloud sql operations list --instance=scalpyn --limit=10 --project=clickrate-477217
gcloud logging read \
  'resource.type="cloudsql_database"
   AND resource.labels.database_id="clickrate-477217:scalpyn"
   AND textPayload =~ "deadlock detected|40P01|canceling statement"' \
  --limit=50 --freshness=2h --format=json --project=clickrate-477217 \
  | python3 -c 'import json,sys; xs=json.load(sys.stdin); print(len(xs), "events"); [print(x["timestamp"], x["textPayload"][:240]) for x in xs[:15]]'
```

A handful of `deadlock detected` per day during the cycle hand-off
between `collect_*` and `compute_*` is *expected churn*. A burst of
**>10 in <10 min** clustered on the same backend PIDs is the symptom
that triggers this runbook.

---

## 1. Identify the statement(s) involved

Postgres logs the *victim* statement on `LOG: process N detected
deadlock`. The *winner* statement only logs at `DEBUG`. Pull both
sides with the surrounding 10 lines:

```bash
gcloud logging read \
  'resource.type="cloudsql_database"
   AND resource.labels.database_id="clickrate-477217:scalpyn"
   AND (textPayload =~ "deadlock detected" OR textPayload =~ "Process \d+ waits for")' \
  --limit=200 --freshness=2h --format=json --project=clickrate-477217 \
  > /tmp/dl.json

python3 - <<'PY'
import json, re
xs = json.load(open("/tmp/dl.json"))
xs.sort(key=lambda x: x["timestamp"])
for x in xs:
    msg = x.get("textPayload", "")
    pid = re.search(r"\[(\d+)\]", msg)
    print(x["timestamp"], (pid.group(1) if pid else "??"), msg[:300].replace("\n", " "))
PY
```

What you are looking for:

* `Process A waits for ShareLock on transaction NNN; blocked by process B.`
* `Process B waits for ShareLock on transaction MMM; blocked by process A.`
* The `DETAIL:` line that follows lists the *relations* involved
  (e.g. `relation "market_metadata"`, `relation "indicators"`).

The relation name + the SQL that follows are the bisect anchors —
they tell you which file owns the unsorted iteration.

Mapping table → owning callsites (curated for the post-#273 audit):

| Relation                           | Pipeline files that UPSERT/UPDATE rows keyed by symbol |
|------------------------------------|--------------------------------------------------------|
| `market_metadata`                  | `collect_market_data.py`, `compute_indicators.py` (1h/30m/5m), schedulers |
| `indicators`                       | `compute_indicators.py` (3 cadences), `persistence/repositories.py` |
| `ohlcv`                            | `collect_market_data.py`, `collect_structural_30m.py`, `ohlcv_backfill_service.py` |
| `alpha_scores`                     | `compute_scores.py` |
| `pipeline_watchlist_assets`        | `pipeline_scan.py` (`_upsert_assets`) |
| `pipeline_watchlist_rejections`    | `pipeline_scan.py` (`_replace_rejection_snapshot`) |
| `decisions_log`                    | `evaluate_signals.py` (per-symbol `_safe_record_decision`), `execute_buy.py` (QUARANTINED + per-symbol `safe_record_decision`), `pipeline_scan.py` (L3 evaluation) |
| `trades` / `pool_coins`            | `evaluate_signals.py` (execute_trade), `execute_buy.py`, `trade_monitor.py` (close_trade) |
| `shadow_trades`                    | `shadow_trade_service.py` (`safe_bulk_create_from_user_skip`, `safe_backfill_watchlist_shadows`) — already sorted by symbol/ID |

---

## 2. Snapshot `pg_stat_activity` + `pg_locks` while it is happening

If the spike is still in progress, this is the highest-signal data:

```bash
gcloud sql connect scalpyn --user="$PGUSER" --database=scalpyn \
  --project=clickrate-477217 <<'SQL'
\timing on

-- Who is currently waiting on whom, with the actual statements.
SELECT
    blocked.pid       AS blocked_pid,
    blocked.application_name AS blocked_app,
    blocked.query     AS blocked_query,
    blocking.pid      AS blocking_pid,
    blocking.application_name AS blocking_app,
    blocking.query    AS blocking_query
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking
  ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
WHERE blocked.datname = 'scalpyn'
  AND blocked.wait_event_type = 'Lock';

-- Which rows of which relation are contended right now.
SELECT
    a.pid,
    a.application_name,
    l.locktype, l.relation::regclass AS relation, l.mode,
    l.granted, NOW() - a.xact_start AS xact_age,
    LEFT(a.query, 200) AS query
FROM pg_locks l
JOIN pg_stat_activity a USING (pid)
WHERE a.datname = 'scalpyn'
  AND l.relation IS NOT NULL
ORDER BY granted, xact_age DESC NULLS LAST
LIMIT 40;
SQL
```

`application_name` carries the Cloud Run service name when set by
`server_settings={"application_name": ...}` in `database.py`. That is
how a PID maps back to a worker (`scalpyn-worker-structural`,
`-micro`, `-execution`, or the API). Without it, fall back to the
timestamp + the service that was scheduled to run at that minute
(beat schedule in `celery_app.py`).

---

## 3. Map a Postgres PID to a Cloud Run revision

Postgres backend PIDs are ephemeral but the matching Cloud Run
revision can be found by timestamp. Take the earliest deadlock log
line in the burst (e.g. `2026-05-11T19:21:47Z`) and cross-reference:

```bash
gcloud run revisions list \
  --service=scalpyn-worker-structural \
  --region=us-central1 \
  --project=clickrate-477217 \
  --format="table(metadata.name, status.conditions[0].lastTransitionTime, status.observedGeneration)" \
  --limit=10
```

Pick the revision whose `lastTransitionTime` is the most recent before
the burst — that is the version of the code that actually ran. Diff
its source against `main` to see if a callsite was added that escaped
the `sorted()` invariant.

---

## 4. Reproduce in staging (optional, for confirmation)

If the offending callsite is ambiguous, briefly enable verbose lock
logging on a staging Cloud SQL instance (NOT prod — `log_statement=all`
is hugely expensive on a hot DB):

```bash
gcloud sql instances patch scalpyn-staging \
  --database-flags=log_lock_waits=on,log_min_duration_statement=200,deadlock_timeout=200 \
  --project=clickrate-477217
```

Replay a representative beat tick (`celery -A app.tasks.celery_app
call app.tasks.collect_market_data.collect_all` with two workers
running) and grep the staging log for `process X still waiting for
RowExclusiveLock` lines. They will name the SQL.

Roll the flags back when done:

```bash
gcloud sql instances patch scalpyn-staging \
  --clear-database-flags --project=clickrate-477217
```

---

## 5. Apply the fix and verify

The fix shape is always the same:

```python
# Task #273: deterministic sort — deadlock-prevention invariant.
for symbol in sorted(symbols):
    ...
```

For dict-based iteration: `sorted(d.items(), key=lambda kv: kv[0])`.
For ticker payloads: `sorted(tickers, key=lambda t: t["currency_pair"])`.

After deploy, confirm zero recurrence over 24h:

```sql
-- Cloud Shell
SELECT date_trunc('hour', log_time) AS hora,
       count(*) FILTER (WHERE message ~* 'deadlock detected') AS deadlocks
FROM postgres_logs
WHERE log_time > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1 DESC;
```

Append the result (HH-by-HH count) to the bottom of this runbook so
the post-fix evidence stays adjacent to the procedure.

---

## 6. Lint test contract (two layers)

`backend/tests/test_pipeline_symbol_ordering_invariants.py` ships
**two** complementary checks; both must stay green:

1. **AST structural invariant** (`test_pipeline_iterates_symbols_in_sorted_order`).
   Parses each file in `_PIPELINE_FILES`, walks every `for` /
   comprehension node, and flags iteration over a known symbol-set
   variable name when the iterable is not wrapped in `sorted(...)`
   AND the variable was not pre-sorted earlier in source / in any
   enclosing function (closure-aware) AND the loop body issues a
   per-row DB write (call to `db.execute`, `session.add`,
   `enqueue_or_log`, etc. — see `_DB_WRITE_CALL_NAMES`). Pure
   in-memory loops (filter, accumulate, payload-build) are exempt
   because they hold no row-locks.

2. **Pinned literal markers** (`test_pipeline_callsite_marker_present`).
   Substring-asserts the *specific* sort statement at every callsite
   we patched in #251 / #273. Catches reverts that rename or restructure
   the sort while still satisfying the AST check (e.g. swapping
   variable names, replacing `sorted()` with a different sorted
   helper).

When adding a new pipeline file that writes per-symbol rows:

1. Add the relative path to `_PIPELINE_FILES` in the lint test.
2. Sort the iteration at the source (`sorted(...)`) and add the
   matching `(file, marker, why)` row to `_REQUIRED_MARKERS`.
3. Add the new relation name to the *Mapping* table in step 1 above.

Opt-out (rare — read-only iteration over symbols where deadlock is
impossible, or the loop is provably single-element): append
`# noqa: deadlock-sort: <reason>` to the loop line. The AST layer
accepts the marker, the comment is the audit trail.

---

## 7. History

* **2026-05-09 14:22-14:31 UTC** — first burst, 140 deadlocks in
  9 min on `market_metadata`. Root cause: 8 callsites in
  `collect_market_data` + 3 schedulers iterating the universe in
  cross order. Fix: Task #251 — `sorted()` in those 8 callsites +
  `_bulk_upsert_market_metadata` helper.
* **2026-05-11 19:21-19:27 UTC** — regression, 10 deadlocks +
  3 cancel-statement on PIDs 1011848 / 1012525. Root cause: the
  `compute_indicators` 1h/30m/5m loops, `compute_scores`, and
  `pipeline_scan._upsert_assets` were never covered by the #251
  patch. Fix: Task #273 — `sorted()` on those 5 callsites + the
  AST lint test that pins the invariant.

* **2026-05-20 — Task #310** — segunda regressão pós-#273. Burst
  de 40P01 voltou após o ciclo de mudanças 2026-05-18+ (Tasks
  #303 shadow live-L3, continuous shadow trading, min_alpha_score
  gate) aumentar a pressão de escrita no worker-execution. Bisect
  feito por inspeção de código (Cloud SQL logs não acessíveis no
  ambiente do agente nesta janela). Suspeitos auditados e
  liberados: `shadow_trade_service.safe_bulk_create_from_user_skip`
  (já `sorted(ids)`), `safe_backfill_watchlist_shadows` (já
  `sorted(eligible_symbols)`), `trade_reconciliation_service`
  (loops sobre connections/trades, não symbol-keyed),
  `persistence/repositories.py` (1 mensagem/tx, sem contenção
  inter-worker).

  **Root cause**: dois call-sites no caminho de execução iterando
  `merged_by_sym.items()` (dict devolvido por
  `get_merged_indicators`, cuja ordem é não-determinística — depende
  da ordem de inserção interna do provider) com `decisions_log`
  INSERT + `execute_trade`/`safe_record_decision` writes no corpo
  do loop:

  - `app/tasks/evaluate_signals.py:326` (`_evaluate_async`)
  - `app/tasks/execute_buy.py:420` (candidate-build / QUARANTINED
    path)

  Worker-execution roda `--concurrency=2`, então dois ticks
  paralelos podem iterar o MESMO set de símbolos em ordens
  opostas → 40P01 em row-locks compartilhados
  (`decisions_log`, `trades`, `pool_coins`).

  **Por que o lint #273 não pegou**: o AST walker original só
  inspecionava `ast.Name` no nó `iter` do `for`. Iteração via
  `dict.items()` (que é um `ast.Call` sobre `ast.Attribute`) era
  silenciosamente ignorada. E `evaluate_signals.py` /
  `execute_buy.py` nem estavam em `_PIPELINE_FILES`.

  **Fix**:
  1. `sorted(merged_by_sym.items())` nos dois call-sites com
     comentário "Task #310".
  2. `evaluate_signals.py` + `execute_buy.py` adicionados a
     `_PIPELINE_FILES`.
  3. Novo helper `_symbol_keyed_dict_iter` no AST walker detecta
     `<name>.items()/.keys()/.values()` em dicts de
     `_SYMBOL_KEYED_DICT_NAMES` (`merged_by_sym`,
     `market_cap_by_sym`, `tradable_by_symbol`, etc.) e exige
     `sorted()` quando o corpo escreve no DB. Fecha o gap
     estrutural — qualquer novo `for k, v in merged_by_sym.items()`
     em arquivo coberto vira erro de lint sem precisar atualizar
     marker.
  4. Dois markers literais novos em `_REQUIRED_MARKERS`.
  5. Mapping table acima estendida com `decisions_log`, `trades`,
     `pool_coins`, `shadow_trades`.
