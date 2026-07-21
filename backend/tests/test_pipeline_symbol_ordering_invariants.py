"""Architectural-invariant lint test for deterministic symbol ordering
before per-row UPSERT/UPDATE inside the pipeline (Task #273, post-#251).

Background
----------
Task #251 fixed a deadlock cascade on ``market_metadata`` by sorting
the symbol universe before iterating in ``collect_market_data`` and
the three schedulers. Postgres acquires row-locks in iteration order,
so two concurrent workers iterating the same set in different orders
produce a deterministic deadlock (40P01) — the SAVEPOINT per symbol
**does not** release row-locks; only the outer COMMIT does.

Task #273 found that other callsites in the pipeline (compute_indicators
1h/30m/5m, compute_scores, pipeline_scan upserts, ohlcv_backfill) still
iterated symbol sets pulled from ``SELECT DISTINCT``/dict ordering with
no sort. The 2026-05-11 19:21–19:27 UTC incident (10 deadlocks +
3 cancel-statement, PIDs 1011848 / 1012525) was the regression those
unsorted callsites caused once the pool grew and ``compute_*`` started
overlapping with ``collect_*`` and the schedulers on the same hot rows.

Two layers of defense
---------------------
This module ships **two** lint tests, both required to pass:

  1. ``test_pipeline_iterates_symbols_in_sorted_order`` — AST walker
     that parses each pipeline file, walks every ``for`` /
     comprehension node, and asserts that any iteration over a known
     symbol-set variable is wrapped in ``sorted(...)`` or pre-sorted.
     This is the structural defense — it catches new callsites added
     in the future, not just the ones we patched today. Closure-aware:
     a name sorted in the enclosing function counts as sorted in the
     inner closure.

  2. ``test_pipeline_callsite_marker_present`` — substring assertion
     that pins the **specific** sort marker each Task #273 / #251
     callsite is supposed to keep. Catches the case where someone
     reverts a sort by replacing it with another expression that the
     AST walker still considers sorted (e.g. switching variable names
     while removing the actual ``sorted`` call). Substring layer is
     the "did anyone touch the fix?" alarm; AST layer is the "is the
     invariant still satisfied?" alarm.

Opt-out: append ``# noqa: deadlock-sort: <reason>`` to the loop line
when iteration is genuinely deadlock-safe (e.g. the iterable is
already a single element, or the loop only reads).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Files that participate in the hot-row pipeline (collector → indicators
# → scoring → upsert) and so MUST sort before per-row writes. Keep this
# list in sync with the runbook
# ``backend/docs/runbooks/postgres-deadlock-bisect.md``.
_PIPELINE_FILES = (
    "app/tasks/collect_market_data.py",
    "app/tasks/collect_structural_30m.py",
    "app/tasks/compute_indicators.py",
    "app/tasks/compute_scores.py",
    "app/tasks/fetch_market_caps.py",
    "app/tasks/pipeline_scan.py",
    # Task #310 (2026-05-20): bisect novo apontou
    # ``evaluate_signals`` + ``execute_buy`` iterando ``merged_by_sym``
    # (dict do ``get_merged_indicators``, ordem não-determinística)
    # com ``decisions_log`` INSERT + ``execute_trade``/QUARANTINED
    # writes no corpo do loop. Worker-execution roda --concurrency=2
    # → dois ticks paralelos podem deadlockar em row-locks
    # compartilhados se o iter order diverge.
    "app/tasks/evaluate_signals.py",
    "app/tasks/execute_buy.py",
    "app/services/scheduler_service.py",
    "app/services/structural_scheduler_service.py",
    "app/services/microstructure_scheduler_service.py",
    "app/services/ohlcv_backfill_service.py",
)

# Iterable variable names that, by convention in this codebase, hold
# a list/set/iterable of trading symbols (or per-symbol payload dicts)
# pulled from a DB SELECT or upstream service AND drive per-row writes.
#
# The set is intentionally NARROW. ``rows`` was excluded after a
# false-positive sweep — it appears too often as a generic SELECT
# result name (OHLCV rows, config rows, metadata rows) where iteration
# does not produce per-symbol UPSERT/UPDATE. ``assets`` and
# ``scored_rows`` are included because in the pipeline files they are
# always per-symbol payloads (audit them when adding to the file list).
_SYMBOL_LIKE_NAMES = frozenset({
    "symbols",
    "valid_symbols",
    "tickers",
    "stale_syms",
    "active_symbols",
    "raw_syms",
    "raw_symbols",
    "pool_symbols",
    "all_symbols",
    "missing_meta",
    "missing_symbols",
    "mm_symbols",
    "pwa_symbols",
    "scored_rows",
    "assets",
    "candidates",
    "sorted_symbols",  # already-sorted form, allowed as iter source
})

# Task #310 (2026-05-20): symbol-keyed dicts whose ``.items()`` / ``.keys()``
# / ``.values()`` iteration must also be ``sorted(...)`` when the loop body
# issues per-row DB writes. Dict iteration order in Python 3.7+ is insertion
# order, and the dicts below come from DB queries / provider internals whose
# row order is non-deterministic — so two concurrent workers iterate the same
# set of symbols in different orders and deadlock. Same root-cause class as
# Tasks #251/#273; this extension closes the ``dict.items()`` gap that the
# original AST walker missed (it only inspected ``ast.Name`` iter nodes).
_SYMBOL_KEYED_DICT_NAMES = frozenset({
    "merged_by_sym",          # get_merged_indicators output
    "market_cap_by_sym",
    "tradable_by_symbol",
    "symbol_market_type",
    "symbol_to_market_type",
})


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _line_has_optout(line: str) -> bool:
    return "noqa: deadlock-sort" in line


def _is_sorted_call(node: ast.AST) -> bool:
    """Return True when ``node`` is a literal ``sorted(...)`` call."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "sorted":
            return True
    return False


def _enclosing_functions(tree: ast.Module, lineno: int) -> list[ast.AST]:
    """Return every enclosing function for ``lineno`` from outermost
    to innermost. Closure-aware: a sort done in an outer function
    applies to inner closures too."""
    out: list[tuple[int, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= lineno <= end:
                out.append((start, node))
    out.sort()
    return [fn for _, fn in out]


def _name_was_sorted_in(
    func_node: ast.AST, target_name: str, before_lineno: int | None = None
) -> bool:
    """Walk ``func_node`` and check whether ``target_name`` was either
    assigned from a ``sorted(...)`` call or had ``.sort(...)`` invoked
    on it. When ``before_lineno`` is given, only consider statements
    that appear earlier in source order."""
    for sub in ast.walk(func_node):
        sub_lineno = getattr(sub, "lineno", 0)
        if before_lineno is not None and sub_lineno >= before_lineno:
            continue
        # ``x = sorted(...)`` (handles single + tuple targets)
        if isinstance(sub, ast.Assign) and _is_sorted_call(sub.value):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and tgt.id == target_name:
                    return True
        # ``x: T = sorted(...)``
        if (
            isinstance(sub, ast.AnnAssign)
            and sub.value is not None
            and _is_sorted_call(sub.value)
            and isinstance(sub.target, ast.Name)
            and sub.target.id == target_name
        ):
            return True
        # ``x.sort(...)``
        if isinstance(sub, ast.Expr):
            call = sub.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "sort"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == target_name
            ):
                return True
    return False


def _name_pre_sorted(
    tree: ast.Module, lineno: int, target_name: str
) -> bool:
    """``target_name`` is considered pre-sorted if it was sorted
    earlier in any enclosing function in source order, OR anywhere
    in any enclosing OUTER function (closure capture: an outer-scope
    assignment binds before the inner function ever runs)."""
    enclosing = _enclosing_functions(tree, lineno)
    if not enclosing:
        return False
    innermost = enclosing[-1]
    # In the innermost function: must appear before the use site.
    if _name_was_sorted_in(innermost, target_name, before_lineno=lineno):
        return True
    # In any outer function: anywhere in the body counts (closure).
    for outer in enclosing[:-1]:
        if _name_was_sorted_in(outer, target_name, before_lineno=None):
            return True
    return False


# Method/function names whose presence in a loop body indicates the loop
# issues per-row DB writes — and therefore must iterate in deterministic
# order. Read-only iterations and pure in-memory transforms (filter,
# accumulate, build response payloads) are not subject to the invariant.
_DB_WRITE_CALL_NAMES = frozenset({
    "execute",          # db.execute(text("INSERT/UPDATE/UPSERT ..."))
    "executemany",
    "add",              # session.add(model)
    "add_all",
    "merge",
    "delete",
    "bulk_insert_mappings",
    "bulk_update_mappings",
    "bulk_save_objects",
    "enqueue_or_log",   # persistence queue write (idempotent UPSERT msg)
    "enqueue",
    # Task #310: execution-path write wrappers that proxy per-row INSERTs
    # into decisions_log / trades / shadow_trades. Adding them here so the
    # AST walker recognizes the loop body as DB-writing even when the
    # raw ``db.execute`` lives behind the helper. Without this, a future
    # ``for k, v in merged_by_sym.items(): await safe_record_decision(...)``
    # would silently slip past the lint (the marker layer would still need
    # to be updated by the author — defeats the "structural defense" goal).
    "safe_record_decision",
    "_safe_record_decision",
    "safe_create_from_symbol_skip",
    "safe_bulk_create_from_user_skip",
    "safe_backfill_watchlist_shadows",
    "_create_from_decision",
    "execute_trade",
    "execute_buy",
    "close_trade",
    "send_trade_alert",  # writes notification rows
})


def _loop_body_has_db_write(node: ast.AST) -> bool:
    """True iff ``node``'s descendants contain a call to one of the
    method names in ``_DB_WRITE_CALL_NAMES``. Used to skip pure
    in-memory loops (filter / accumulate / payload-build) which are
    deadlock-safe regardless of iteration order."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr in _DB_WRITE_CALL_NAMES:
                return True
            if isinstance(func, ast.Name) and func.id in _DB_WRITE_CALL_NAMES:
                return True
    return False


def _symbol_keyed_dict_iter(iter_node: ast.AST) -> str | None:
    """Return the dict name when ``iter_node`` is
    ``<name>.items()`` / ``.keys()`` / ``.values()`` on a known
    symbol-keyed dict (Task #310 lint gap). Returns None otherwise."""
    if not isinstance(iter_node, ast.Call):
        return None
    func = iter_node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in {"items", "keys", "values"}:
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if func.value.id not in _SYMBOL_KEYED_DICT_NAMES:
        return None
    return func.value.id


def _check_for_loop(
    node: ast.For | ast.AsyncFor, tree: ast.Module, lines: list[str]
) -> str | None:
    iter_node = node.iter
    if _is_sorted_call(iter_node):
        return None
    line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
    if _line_has_optout(line):
        return None
    # Path A: ``for x in <symbol_like_name>:``
    if isinstance(iter_node, ast.Name) and iter_node.id in _SYMBOL_LIKE_NAMES:
        if _name_pre_sorted(tree, node.lineno, iter_node.id):
            return None
        if not _loop_body_has_db_write(node):
            return None
        return (
            f"line {node.lineno}: ``for ... in {iter_node.id}:`` issues per-row "
            "DB writes without a sort — wrap with sorted(...) or pre-sort the "
            "variable; see Task #273 invariant."
        )
    # Path B (Task #310): ``for k, v in <symbol_keyed_dict>.items():``
    dict_name = _symbol_keyed_dict_iter(iter_node)
    if dict_name is not None:
        if not _loop_body_has_db_write(node):
            return None
        return (
            f"line {node.lineno}: ``for ... in {dict_name}.items()`` (or "
            ".keys()/.values()) issues per-row DB writes without a sort — wrap "
            "with sorted(...) so iteration order is deterministic across "
            "concurrent workers; see Task #310 / #273 invariant."
        )
    return None


def _check_comprehension(
    node: ast.AST, tree: ast.Module, lines: list[str]
) -> list[str]:
    """``[expr for s in symbols]`` and friends — typically the spread
    inside ``asyncio.gather(*[...])``. Same invariant applies."""
    offences: list[str] = []
    if not isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        return offences
    for gen in node.generators:
        if _is_sorted_call(gen.iter):
            continue
        if not isinstance(gen.iter, ast.Name):
            continue
        if gen.iter.id not in _SYMBOL_LIKE_NAMES:
            continue
        lineno = node.lineno
        line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
        if _line_has_optout(line):
            continue
        if _name_pre_sorted(tree, lineno, gen.iter.id):
            continue
        # Comprehensions are pure-expression by construction in Python:
        # if there is no DB-write call inside the element/filter
        # expression, the comprehension cannot itself drive per-row
        # locks. (The classic offender — building a list of payload
        # dicts as input to a downstream UPSERT — is caught either by
        # the AST check on the loop that consumes that list, or by the
        # required-marker layer below.)
        if not _loop_body_has_db_write(node):
            continue
        offences.append(
            f"line {lineno}: comprehension iterates ``{gen.iter.id}`` "
            "without sorted(...) — wrap with sorted(...) or pre-sort the "
            "variable; see Task #273 invariant."
        )
    return offences


@pytest.mark.parametrize("rel_path", _PIPELINE_FILES)
def test_pipeline_iterates_symbols_in_sorted_order(rel_path: str) -> None:
    """AST-based structural invariant — defense layer #1."""
    path = _BACKEND_ROOT / rel_path
    assert path.exists(), f"missing pipeline file: {rel_path}"
    src = path.read_text(encoding="utf-8")
    lines = _read_lines(path)
    tree = ast.parse(src, filename=str(path))

    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            offence = _check_for_loop(node, tree, lines)
            if offence:
                offences.append(offence)
        offences.extend(_check_comprehension(node, tree, lines))

    assert not offences, (
        f"{rel_path}: deterministic-sort invariant violated (Task #273). "
        "Postgres acquires row-locks in iteration order — two concurrent "
        "workers iterating the same symbol set in different orders cause "
        "deterministic deadlock 40P01 on hot tables (market_metadata, "
        "indicators, alpha_scores, pipeline_watchlist_assets). "
        "If the iteration is genuinely deadlock-safe (read-only, single "
        "element, etc.), append ``# noqa: deadlock-sort: <reason>`` to "
        "the loop line. See backend/docs/runbooks/postgres-deadlock-bisect.md.\n  "
        + "\n  ".join(offences)
    )


# ── Defense layer #2: pinned literal markers for each known callsite ──
#
# (file_path, marker, why) — every marker MUST appear at least once in
# the file. Markers are literal substrings (no regex). The "why" string
# is included in the assertion failure to give the operator the table
# that would deadlock without the sort.
_REQUIRED_MARKERS: tuple[tuple[str, str, str], ...] = (
    # ── Task #251 callsites ──
    (
        "app/tasks/collect_market_data.py",
        "symbols = sorted(valid_symbols)",
        "collect_5m / collect_all symbol loop UPSERTs market_metadata",
    ),
    (
        "app/tasks/collect_market_data.py",
        'valid_rows.sort(key=lambda r: r["symbol"])',
        "1h+5m ticker bulk UPSERT path acquires row-locks in tuple order",
    ),
    (
        "app/tasks/collect_market_data.py",
        "for sym in sorted(stale_syms):",
        "fallback per-symbol stale UPSERT path on market_metadata",
    ),
    (
        "app/services/scheduler_service.py",
        "for s in sorted(symbols)",
        "combined scheduler gather acquires row-locks via _refresh_one_symbol",
    ),
    (
        "app/services/structural_scheduler_service.py",
        "for s in sorted(symbols)",
        "structural scheduler gather acquires row-locks on market_metadata",
    ),
    (
        "app/services/microstructure_scheduler_service.py",
        "for s in sorted(symbols)",
        "microstructure scheduler gather acquires row-locks on market_metadata",
    ),
    (
        "app/tasks/collect_structural_30m.py",
        "symbols = sorted(valid_symbols)",
        "30m structural collect symbol loop UPSERTs market_metadata + ohlcv",
    ),

    # ── Task #273 new callsites ──
    (
        "app/tasks/compute_indicators.py",
        "symbols = sorted(row.symbol for row in symbols_result.fetchall())",
        "compute_indicators 1h + 30m loops UPSERT market_metadata + INSERT indicators",
    ),
    (
        "app/tasks/compute_indicators.py",
        "symbols = sorted(row.symbol for row in symbol_rows)",
        "compute_indicators 5m loop UPSERTs market_metadata + INSERT indicators",
    ),
    (
        "app/tasks/compute_scores.py",
        "rows.sort(key=lambda r: r.symbol)",
        "compute_scores INSERT into alpha_scores must order rows deterministically",
    ),
    (
        "app/tasks/compute_scores.py",
        "ORDER BY pwa.symbol",
        "_detect_level_transitions SELECT must ORDER BY symbol so per-row UPDATEs lock deterministically",
    ),
    (
        "app/tasks/fetch_market_caps.py",
        'mm_symbols = sorted(await _get_distinct_symbols(db, "market_metadata"))',
        "fetch_market_caps UPDATEs market_metadata in the same deterministic order as compute_5m",
    ),
    (
        "app/tasks/fetch_market_caps.py",
        "pwa_symbols = sorted(",
        "fetch_market_caps UPDATEs pipeline_watchlist_assets while holding outer-transaction row locks",
    ),
    (
        "app/tasks/pipeline_scan.py",
        'assets_sorted = sorted(assets, key=lambda a: a.get("symbol", ""))',
        "_upsert_assets writes per-row to pipeline_watchlist_assets keyed by (watchlist_id, symbol)",
    ),
    (
        "app/tasks/pipeline_scan.py",
        'rows = sorted(rows, key=lambda r: r.get("symbol", ""))',
        "_replace_rejection_snapshot inserts pipeline_watchlist_rejections per symbol",
    ),
    (
        "app/services/ohlcv_backfill_service.py",
        "sorted_symbols = sorted(symbols)",
        "ohlcv_backfill_service iterates symbols UPSERTing into ohlcv hypertable; sorted_symbols also keeps result mapping aligned",
    ),

    # ── Task #310 callsites (2026-05-20) ──
    (
        "app/tasks/evaluate_signals.py",
        "for symbol, mi in sorted(merged_by_sym.items()):",
        "evaluate_signals.evaluate loop emits per-symbol INSERTs in decisions_log "
        "and calls execute_trade; worker-execution runs --concurrency=2 so two "
        "ticks iterating the same merged_by_sym dict in different orders can "
        "deadlock on shared row-locks (decisions_log index pages, trades, pool state)",
    ),
    (
        "app/tasks/execute_buy.py",
        "for _sym, _mi in sorted(merged_by_sym.items()):",
        "execute_buy candidate-build loop issues safe_record_decision INSERTs for "
        "QUARANTINED symbols and feeds the downstream per-row execute path; same "
        "non-deterministic dict ordering risk as evaluate_signals",
    ),
)


@pytest.mark.parametrize(
    "rel_path,marker,why",
    _REQUIRED_MARKERS,
    ids=[f"{p}::{m[:60]}" for p, m, _ in _REQUIRED_MARKERS],
)
def test_pipeline_callsite_marker_present(
    rel_path: str, marker: str, why: str
) -> None:
    """Substring-pinned regression check — defense layer #2."""
    path = _BACKEND_ROOT / rel_path
    assert path.exists(), f"missing pipeline file: {rel_path}"
    src = path.read_text(encoding="utf-8")
    assert marker in src, (
        f"{rel_path}: required deadlock-prevention sort marker missing.\n"
        f"  Expected substring : {marker!r}\n"
        f"  Why it matters     : {why}\n"
        "  Postgres acquires row-locks in iteration order — two concurrent "
        "workers iterating the same symbol set in different orders cause "
        "deterministic deadlock 40P01 on hot tables. Restore the sort "
        "before merging. See backend/docs/runbooks/postgres-deadlock-bisect.md "
        "and Task #251 / #273 for context."
    )
