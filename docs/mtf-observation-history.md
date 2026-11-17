# Histórico de observações MTF

Capture inputs prospectively, without granting trading or additional Shadow authority.
The existing MTF/Shadow observer is unchanged. `mtf_history` is a dedicated queue;
it cannot be combined with indicator workers. The recovery beat explicitly allows
only its observational dispatch and maintenance tasks.

The universe uses profile display membership, including data-pending entries,
independently of Shadow capacity. Non-consolidated watchlists retain every current
member. Consolidated membership retains the same representative profile as the
display. Collection follows the watchlists already monitored in MTF settings.

`mtf_history_capture_jobs` is a durable outbox. Retried deliveries are idempotent;
unacknowledged jobs are re-dispatched after the lease expires. Actual capture time
is recorded separately from scheduled time: delayed captures are never backdated.
The capture-health endpoint exposes pending/error work. A provider failure can
leave work pending; it must not be presented as complete coverage.

`mtf_history_observations` is append-only, enforced by a database trigger. Inputs
include the frozen engine/profile configuration, closed-candle feature values and
provenance, source producer envelopes marked context-only, MTF states/reasons,
zones, pivots, patterns and available market context. Missing values remain null.
Gate ticker response receipt is a local timestamp; exchange event time is unknown.
Observation price and closed-candle price are separate. Producer status is not
treated as proof of freshness; source age and analytical quality are recorded.

Future labels are separate rows, never features. They follow all observation
states, even after membership removal. Prices/highs/lows use complete closed Gate
candles whose opening is after the observation. This excludes the partially
observed candle. The endpoint is the first candle close at/after the target.
Actual coverage and source times are exported. Missing candles produce INCOMPLETE,
not fabricated returns. Baseline is the received quote, otherwise the identified
fresh closed-candle price. Descriptive returns exclude transaction costs and are
not simulated trade fills. Unmatured horizons stay PENDING. Status transitions
describe subsequent observations, not a continuously monitored exchange event.

`mtf_history` settings are separate from `mtf_analysis` and trading profiles.
Changing analytical horizons does not change old observations or engine hashes.
No retention/delete schedule or automatic ML promotion is installed.

Authenticated endpoints under `/api/mtf/observations` provide list, detail,
settings, capture-health and asynchronous exports. Every read/download includes
the owner's user ID predicate, independent of current watchlist membership.
CSV ZIP contains observations/indicators/outcomes. Excel has equivalent sheets;
oversized JSON is preserved in ordered `metadata_chunks`, referenced by the cell.
Concatenate chunks in index order to reconstruct that original cell. Exports
include every row matching the applied filters at request-time cutoff, not just
the loaded UI page. Export errors are explicit and retryable via a new request.

Legacy chart evaluations remain available in the existing chart history. They
are not backfilled with indicators that were never recorded. Production proof
must verify automatic capture, tenant isolation, download, preserved profile hash,
recovery observation guards and no real orders. Longer outcomes are validated as
pending until market time matures; synthetic fixtures verify their calculations.
