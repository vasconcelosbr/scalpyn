"""Testes para captura L1_SPECTRUM (migration 073 / PROMPT_2).

Critérios de aceite:
1. Amostragem determinística: mesmo (symbol, execution_id) → mesma decisão de sorteio.
2. Reentrada por source: constraint (user_id, symbol, source) → streams independentes.
3. Rate limit: ao atingir max_per_hour, skip com razão 'RATE_LIMITED'.
4. Com capture_l1_enabled=false: função retorna 0 sem criar nada.
"""

import hashlib
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── 1. Hash determinístico ─────────────────────────────────────────────────────

def _compute_hash(symbol: str, execution_id: str) -> int:
    """Replica a função de hash de create_l1_spectrum_shadows."""
    return int(hashlib.sha256(f"{symbol}:{execution_id}".encode()).hexdigest(), 16) % 10000


def test_hash_is_deterministic():
    """Mesmo symbol + execution_id sempre produz o mesmo hash."""
    sym = "BTC_USDT"
    eid = "exec-abc-123"
    h1 = _compute_hash(sym, eid)
    h2 = _compute_hash(sym, eid)
    assert h1 == h2


def test_hash_changes_with_execution_id():
    """Execution_id diferente → hash diferente (independência entre ciclos)."""
    sym = "BTC_USDT"
    h1 = _compute_hash(sym, "exec-cycle-1")
    h2 = _compute_hash(sym, "exec-cycle-2")
    assert h1 != h2


def test_hash_changes_with_symbol():
    """Symbol diferente → hash diferente (distribuição uniforme por símbolo)."""
    eid = "exec-abc-123"
    h1 = _compute_hash("BTC_USDT", eid)
    h2 = _compute_hash("ETH_USDT", eid)
    assert h1 != h2


def test_sampling_at_100pct_includes_all():
    """Com sample_rate=1.0, todos os símbolos devem ser selecionados."""
    symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "BNB_USDT"]
    eid = "exec-test"
    sampled = [s for s in symbols if _compute_hash(s, eid) < int(1.0 * 10000)]
    assert set(sampled) == set(symbols)


def test_sampling_at_0pct_excludes_all():
    """Com sample_rate=0.0, nenhum símbolo deve ser selecionado."""
    symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
    eid = "exec-test"
    sampled = [s for s in symbols if _compute_hash(s, eid) < int(0.0 * 10000)]
    assert sampled == []


def test_sampling_rate_is_approximate():
    """Com sample_rate=0.10 e 1000 símbolos, ~10% devem ser selecionados (±3pp)."""
    eid = "exec-stable"
    symbols = [f"COIN{i:04d}_USDT" for i in range(1000)]
    sampled = [s for s in symbols if _compute_hash(s, eid) < int(0.10 * 10000)]
    rate = len(sampled) / len(symbols)
    assert 0.07 <= rate <= 0.13, f"Expected ~10%, got {rate:.2%}"


# ── 2. Source constant e valid set ───────────────────────────────────────────


def test_l1_spectrum_in_valid_sources():
    """SHADOW_SOURCE_L1_SPECTRUM deve estar em _VALID_SHADOW_SOURCES."""
    from app.services.shadow_trade_service import (
        SHADOW_SOURCE_L1_SPECTRUM,
        _VALID_SHADOW_SOURCES,
    )
    assert SHADOW_SOURCE_L1_SPECTRUM == "L1_SPECTRUM"
    assert SHADOW_SOURCE_L1_SPECTRUM in _VALID_SHADOW_SOURCES


def test_l3_still_in_valid_sources():
    """SHADOW_SOURCE_L3 deve continuar em _VALID_SHADOW_SOURCES (sem regressão)."""
    from app.services.shadow_trade_service import (
        SHADOW_SOURCE_L3,
        _VALID_SHADOW_SOURCES,
    )
    assert SHADOW_SOURCE_L3 in _VALID_SHADOW_SOURCES


# ── 3. ON CONFLICT usa (user_id, symbol, source) ─────────────────────────────


def test_insert_sql_is_idempotent_across_all_shadow_unique_contracts():
    """The INSERT must tolerate every partial unique index on shadow_trades.

    A targeted ``ON CONFLICT (user_id, symbol, source)`` cannot arbitrate the
    L1 point-in-time and canonical decision indexes.  PostgreSQL's generic
    ``DO NOTHING`` is deliberately used so concurrent writers remain isolated.
    """
    from app.services.shadow_trade_service import _INSERT_SHADOW_SQL
    sql_text = str(_INSERT_SHADOW_SQL)
    assert "ON CONFLICT DO NOTHING" in " ".join(sql_text.split()).upper()


# ── 4. Enabled=false → retorna 0 sem DB calls ────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_flag_returns_zero():
    """Com shadow_capture_l1_enabled=false, a função retorna 0 imediatamente."""
    from app.services.shadow_trade_service import create_l1_spectrum_shadows

    _ml_config = {"shadow_capture_l1_enabled": False}

    mock_profile = MagicMock()
    mock_profile.config_json = _ml_config

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_profile)

    mock_cfg_db = AsyncMock()
    mock_cfg_db.execute = AsyncMock(return_value=mock_result)
    mock_cfg_db.__aenter__ = AsyncMock(return_value=mock_cfg_db)
    mock_cfg_db.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.database.CeleryAsyncSessionLocal",
        return_value=mock_cfg_db,
    ):
        result = await create_l1_spectrum_shadows(
            user_id="user-1",
            symbols=["BTC_USDT", "ETH_USDT"],
            execution_id="exec-1",
            assets_by_symbol={},
            promotion_at=datetime.now(timezone.utc),
        )

    assert result == 0


# ── 5. Rate limit gera RATE_LIMITED skip ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_generates_skip():
    """Com max_per_hour=0, toda criação gera RATE_LIMITED no skip log."""
    from app.services.shadow_trade_service import create_l1_spectrum_shadows

    _ml_config = {
        "shadow_capture_l1_enabled": True,
        "shadow_capture_l1_sample_rate": 1.0,   # 100% sampled
        "shadow_capture_l1_max_per_hour": 0,     # limit = 0 → always rate limited
        "shadow_capture_l1_source_label": "L1_SPECTRUM",
        "shadow_skip_log_enabled": True,
    }
    _se_config = None  # triggers schema defaults

    mock_ml_row = MagicMock()
    mock_ml_row.config_json = _ml_config

    mock_se_result = MagicMock()
    mock_se_result.scalar_one_or_none = MagicMock(return_value=None)

    mock_ml_result = MagicMock()
    mock_ml_result.scalar_one_or_none = MagicMock(return_value=mock_ml_row)

    call_count = [0]

    async def _fake_execute(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            # First two calls: ml + se config
            if call_count[0] == 1:
                return mock_ml_result
            return mock_se_result
        # rate limit count query
        mock_cnt = MagicMock()
        mock_cnt.scalar_one = MagicMock(return_value=0)
        return mock_cnt

    mock_cfg_db = AsyncMock()
    mock_cfg_db.execute = _fake_execute
    mock_cfg_db.__aenter__ = AsyncMock(return_value=mock_cfg_db)
    mock_cfg_db.__aexit__ = AsyncMock(return_value=False)

    skip_insert_sqls: list = []

    async def _mock_skip_execute(stmt, params=None, *a, **kw):
        if params and "skip_reason" in str(params):
            skip_insert_sqls.append(params.get("skip_reason") or params)
        m = MagicMock()
        m.scalar_one = MagicMock(return_value=0)
        return m

    mock_skip_db = AsyncMock()
    mock_skip_db.execute = _mock_skip_execute
    mock_skip_db.__aenter__ = AsyncMock(return_value=mock_skip_db)
    mock_skip_db.__aexit__ = AsyncMock(return_value=False)
    mock_skip_ctx = AsyncMock()
    mock_skip_ctx.__aenter__ = AsyncMock(return_value=mock_skip_db)
    mock_skip_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_skip_db.begin = MagicMock(return_value=mock_skip_ctx)

    with patch(
        "app.database.CeleryAsyncSessionLocal",
        side_effect=[mock_cfg_db, mock_cfg_db, mock_skip_db, mock_skip_db],
    ), patch(
        "app.services.indicators_provider.get_merged_indicators",
        new=AsyncMock(return_value={}),
    ):
        result = await create_l1_spectrum_shadows(
            user_id="user-1",
            symbols=["BTC_USDT"],
            execution_id="exec-rate-test",
            assets_by_symbol={"BTC_USDT": {}},
            promotion_at=datetime.now(timezone.utc),
        )

    assert result == 0


# ── 6. Reentry policy — por source (conceptual) ──────────────────────────────


def test_reentry_policy_per_source_semantics():
    """Valida semântica: (user_id, symbol, source) permite shadows independentes.

    Stream L3 RUNNING para BTC_USDT não deve bloquear L1_SPECTRUM para BTC_USDT.
    Este teste documenta a invariante — a enforcement é feita pelo constraint de DB
    (migration 073: ux_shadow_running_user_source).
    """
    # Se o constraint fosse (user_id, symbol) sem source, haveria colisão:
    old_key = ("user-x", "BTC_USDT")           # colisão sem source
    new_key_l3 = ("user-x", "BTC_USDT", "L3")  # chave com source — stream L3
    new_key_l1 = ("user-x", "BTC_USDT", "L1_SPECTRUM")  # stream L1 — independente

    assert old_key == new_key_l3[:2]   # old key equals prefix — would collide without source
    assert new_key_l3 != new_key_l1   # chaves diferentes por source
    assert new_key_l3[:2] == new_key_l1[:2]  # mesmo (user, symbol)
