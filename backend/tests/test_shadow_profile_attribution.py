from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.shadow_trades import _to_read
from app.services.shadow_trade_service import (
    _create_from_decision,
    _is_l1_duplicate_conflict,
    _merge_ml_shadow_config,
    _resolve_shadow_tp_pct,
)


class _NonSuppressingNested:
    """begin_nested() de teste: propaga a exceção (não suprime), como o
    savepoint real do SQLAlchemy — __aexit__ retorna False."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _integrity_error(constraint):
    orig = SimpleNamespace(diag=SimpleNamespace(constraint_name=constraint))
    return IntegrityError(f"INSERT INTO shadow_trades ... {constraint}", {}, orig)


def test_shadow_trade_list_item_exposes_profile_attribution():
    profile_id = uuid4()
    row = SimpleNamespace(
        id=uuid4(),
        symbol="BTC_USDT",
        direction="SPOT",
        entry_price=100.0,
        tp_price=102.0,
        sl_price=99.0,
        amount_usdt=25.0,
        outcome=None,
        pnl_pct=None,
        pnl_usdt=None,
        status="RUNNING",
        skip_reason=None,
        holding_seconds=60,
        created_at=None,
        completed_at=None,
        entry_timestamp=None,
        profile_id=profile_id,
        profile_name="generated-profile",
        btc_price_at_entry=None,
        btc_change_1h_pct=None,
        funding_rate_at_entry=None,
        n_concurrent_signals=None,
        mae_pct=None,
        mfe_pct=None,
        max_drawdown_pct=None,
        max_profit_pct=None,
    )

    item = _to_read(row, current_price=101.0)

    assert item.profile_id == profile_id
    assert item.profile_name == "generated-profile"


@pytest.mark.asyncio
async def test_create_from_decision_copies_profile_attribution():
    profile_id = uuid4()
    profile_version = datetime(2026, 6, 19, tzinfo=timezone.utc)
    entry_time = datetime(2026, 6, 19, 1, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        id=123,
        user_id=uuid4(),
        symbol="BTC_USDT",
        strategy="profile-signal",
        direction="SPOT",
        created_at=entry_time,
        metrics={},
        profile_id=profile_id,
        profile_version=profile_version,
        profile_name="generated-profile",
    )

    # first() → None cobre o guard _has_active_profile_shadow (nenhum shadow
    # ativo do profile); mappings().first() → None cobre o resolver de lineage
    # V2 (profile_versions sem match); fetchone() cobre o RETURNING id do INSERT.
    result = SimpleNamespace(
        fetchone=lambda: (uuid4(),),
        first=lambda: None,
        mappings=lambda: SimpleNamespace(first=lambda: None),
    )
    db = AsyncMock()
    db.execute.return_value = result
    # begin_nested() é usado como async context manager (savepoint do INSERT);
    # MagicMock fornece __aenter__/__aexit__ automaticamente.
    db.begin_nested = MagicMock()

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={},
        ),
    ):
        created_id = await _create_from_decision(
            db,
            decision,
            "NOT_TRADABLE",
            {"tp_pct": 2.0, "sl_pct": 1.0},
        )

    assert created_id is not None
    params = db.execute.await_args.args[1]
    assert params["profile_id"] == profile_id
    assert params["profile_version"] == profile_version
    assert params["profile_name"] == "generated-profile"
    assert params["strategy_type"] == "PROFILE_L3"


@pytest.mark.asyncio
async def test_create_from_legacy_decision_keeps_profile_fields_null():
    entry_time = datetime(2026, 6, 19, 1, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        id=124,
        user_id=uuid4(),
        symbol="ETH_USDT",
        strategy="global-signal",
        direction="SPOT",
        created_at=entry_time,
        metrics={},
    )

    # first() → None cobre o guard _has_active_profile_shadow (nenhum shadow
    # ativo do profile); mappings().first() → None cobre o resolver de lineage
    # V2 (profile_versions sem match); fetchone() cobre o RETURNING id do INSERT.
    result = SimpleNamespace(
        fetchone=lambda: (uuid4(),),
        first=lambda: None,
        mappings=lambda: SimpleNamespace(first=lambda: None),
    )
    db = AsyncMock()
    db.execute.return_value = result
    # begin_nested() é usado como async context manager (savepoint do INSERT);
    # MagicMock fornece __aenter__/__aexit__ automaticamente.
    db.begin_nested = MagicMock()

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={},
        ),
    ):
        await _create_from_decision(
            db,
            decision,
            "NOT_TRADABLE",
            {"tp_pct": 2.0, "sl_pct": 1.0},
        )

    params = db.execute.await_args.args[1]
    assert params["profile_id"] is None
    assert params["profile_version"] is None
    assert params["profile_name"] is None
    assert params["strategy_type"] is None


def test_l1_duplicate_conflict_detection():
    """Fase 1.3 — o detector reconhece o índice L1 (via constraint_name e via
    fallback de mensagem) e ignora outros constraints."""
    assert _is_l1_duplicate_conflict(_integrity_error("ux_shadow_l1_symbol_entry"))
    assert not _is_l1_duplicate_conflict(_integrity_error("some_other_constraint"))
    # fallback de mensagem: diag sem constraint_name, mas o erro do driver
    # (orig) carrega o nome no texto (caso asyncpg real).
    orig = Exception(
        'duplicate key value violates unique constraint '
        '"ux_shadow_l1_symbol_entry"'
    )
    bare = IntegrityError("INSERT INTO shadow_trades ...", {}, orig)
    assert _is_l1_duplicate_conflict(bare)


@pytest.mark.asyncio
async def test_create_from_decision_idempotent_on_l1_duplicate():
    """Passo 2.5 — segundo scan que colide na chave natural L1 vira skip
    idempotente: _create_from_decision retorna None, sem exceção não tratada."""
    entry_time = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        id=200, user_id=uuid4(), symbol="DEXE_USDT", strategy="l1-signal",
        direction="SPOT", created_at=entry_time, metrics={},
    )
    db = AsyncMock()
    db.begin_nested = lambda: _NonSuppressingNested()
    # O INSERT (única query nesta rota legacy sem profile) colide no índice L1.
    db.execute.side_effect = _integrity_error("ux_shadow_l1_symbol_entry")

    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={},
        ),
    ):
        result = await _create_from_decision(
            db, decision, "NOT_TRADABLE", {"tp_pct": 2.0, "sl_pct": 1.0},
        )

    assert result is None


def test_strategy_tp_wins_over_legacy_ml_shadow_override():
    """Legacy ML metadata must not override the Strategies Module TP."""
    assert _resolve_shadow_tp_pct({"tp_pct": 0.6, "shadow_tp_pct": 1.5}) == 0.6


def test_merge_ml_shadow_config_carries_full_economic_contract():
    merged = _merge_ml_shadow_config(
        {"tp_pct": 0.6, "sl_pct": 1.0},
        {
            "shadow_tp_pct": 1.5,
            "shadow_barrier_mode": "ATR_DYNAMIC",
            "shadow_atr_multiplier_sl": 1.5,
            "shadow_barrier_min_pct": 0.5,
            "shadow_barrier_max_pct": 3.0,
            "ml_fee_roundtrip_pct": 0.2,
        },
    )

    assert merged["tp_pct"] == 0.6
    assert "shadow_tp_pct" not in merged
    assert merged["shadow_barrier_mode"] == "ATR_DYNAMIC"
    assert merged["sl_atr_multiplier"] == 1.5
    assert merged["ml_fee_roundtrip_pct"] == 0.2
