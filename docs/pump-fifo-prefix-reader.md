# Pump FIFO prefix reader

The previous reader decompressed candidate observation documents and costed all
candidate price windows before finding the admitted FIFO prefix. The new reader
selects indexed queue metadata first, resolves one frozen specification per hash,
then walks requests in their existing order. Each instrument/minute is costed once.
The walk stops after the first identity exceeding either existing read budget.
Only admitted price windows and observation documents are returned to the labeller.
Multiple admitted horizons share their immutable observation document.

Costing and selection of complete/latest minute revisions use one PostgreSQL
statement snapshot. Missing minutes cost zero; the unchanged labeller preserves
their gaps and unknown outcomes. A single oversized head keeps the existing
resource block and explicit review policy. Label insert and queue completion stay
atomic and idempotent. The diagnostic `examined` counts candidate identities
actually inspected; exhausted-batch diagnostics describe examined costs, not
the uninspected suffix.

No raw records, frozen label specifications, thresholds, cadence, concurrency,
workers, provider limits or ML gates change. In particular, the existing point,
byte and deadline limits are retained. No JIT setting or database migration is
introduced. A lower SQL cost does not increase the point budget or prove that
the backlog will drain.

## Validation evidence

- Focused regression suite: `199 passed` [test output].
- SELECT-only PostgreSQL fixtures: `21 passed` [test output], including overlapping
  windows, revisions, missing instruments, mixed resolutions, point/byte limits
  and an invalid discarded suffix which must never be costed.
- Real repeatable-read snapshot: `requested=512`, `accepted=301`,
  `examined=302`, `labels_equal=301` [query]. The old bounded oracle received
  the same inspected prefix; all returned paths and complete label objects matched.
- Full-candidate server benchmark on a separate repeatable-read snapshot at
  `2026-10-02T15:28:59.107180+00:00` [query], with unchanged statement timeout.
  These are execution times from `EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF)`,
  not HTTP latency or a representative workload distribution:

| Reader | First measurement, ms [query] | Repeat, ms [query] |
| --- | ---: | ---: |
| Previous, all candidates | 367.208 | 139.965 |
| FIFO prefix, all candidates | 117.410 | 114.008 |

Both benchmark statements returned `2823` rows and examined a candidate input
of `512` [query]. Do not attribute changes in accepted count across different
snapshots to this optimization: it depends on horizon length, density and overlap.
Several remote full-payload reads hit the existing timeout during development;
the successful server benchmark is not evidence that all timeouts are eliminated.

Post-deploy acceptance requires sustained completion versus actual maturation,
declining due count/oldest age, and bounded error/capture freshness checks.
If capacity remains insufficient, report the measured deficit before proposing
changes to budgets, cadence or resources.

## Evidence ledger

| Reported number | Source | Literal value |
| --- | --- | --- |
| Focused tests | pytest | `199 passed in 6.95s` |
| PostgreSQL fixtures | pytest, SELECT-only | `21 passed in 203.62s` |
| Live equivalence | reader proof | `requested:512, accepted:301, examined_count:302, labels_equal:301` |
| Server timings | EXPLAIN ANALYZE | `367.208,117.410,114.008,139.965` |
| Server benchmark support | EXPLAIN ANALYZE | `candidates:512, returned_rows:2823` |

Run the optional SQL fixtures with `PUMP_READER_TEST_DATABASE_URL` supplied
through the environment. They shadow the table name with in-memory CTE data,
set the transaction READ ONLY, and create no database objects. CI without that
environment runs the regression contracts and reports these integration cases
as skipped; it does not substitute for the recorded PostgreSQL proof.
