"""
Skill Profiles — Pre-built trading strategy templates for the Market Skills Engine.

Each skill defines:
  - Scoring rules with weighted points (replaces binary vetos)
  - Risk-only block rules (only operational risks, not indicator-based vetos)
  - Regime affinity (which regimes this skill excels in)
  - Buy/hold/reject thresholds

Skills are seeded as system defaults and can be customized per user.
The SkillSelector chooses the optimal skill based on current regime.

Author: Market Skills Engine v1
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .market_regime_engine import MarketRegime

logger = logging.getLogger(__name__)


# ── SkillProfile dataclass ────────────────────────────────────────────────────

@dataclass
class SkillProfile:
    """Runtime representation of a trading skill profile."""
    skill_key: str
    name: str
    description: str = ""
    regime_affinity: List[MarketRegime] = field(default_factory=list)
    scoring_rules: List[Dict[str, Any]] = field(default_factory=list)
    scoring_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "strong_buy": 80, "buy": 60, "neutral": 40,
    })
    block_rules: List[Dict[str, Any]] = field(default_factory=list)
    performance_history: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    db_id: Optional[str] = None

    def to_profile_config(self) -> Dict[str, Any]:
        """Converts to the existing ProfileEngine config format."""
        return {
            "scoring": {
                "thresholds": self.scoring_thresholds,
                "rules": self.scoring_rules,
            },
            "block_rules": {
                "blocks": self.block_rules,
            },
            "entry_triggers": {
                "logic": "AND",
                "conditions": [],  # Skills use scoring, not binary triggers
            },
            "filters": {
                "logic": "AND",
                "conditions": [],  # Skills use scoring, not binary filters
            },
            "signals": {
                "logic": "AND",
                "conditions": [],
            },
            "_skill_metadata": {
                "skill_key": self.skill_key,
                "name": self.name,
                "regime_affinity": [r.value for r in self.regime_affinity],
            },
        }

    def classify_score(self, score: float) -> str:
        """Classify a score into a trading decision."""
        if score >= self.scoring_thresholds.get("strong_buy", 80):
            return "STRONG_BUY"
        elif score >= self.scoring_thresholds.get("buy", 60):
            return "BUY"
        elif score >= self.scoring_thresholds.get("neutral", 40):
            return "HOLD"
        else:
            return "REJECT"


# ── Skill Templates ──────────────────────────────────────────────────────────

SKILL_TEMPLATES: Dict[str, Dict[str, Any]] = {

    # ── MEAN REVERSION ────────────────────────────────────────────────────
    "mean_reversion": {
        "name": "Mean Reversion",
        "description": (
            "Compra correções e retornos à média. "
            "Ideal em mercados laterais onde preços oscilam ao redor de médias móveis."
        ),
        "regime_affinity": [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLATILITY],
        "scoring_thresholds": {"strong_buy": 75, "buy": 55, "neutral": 35},
        "scoring_rules": [
            # Momentum — procura sobrevenda
            {"indicator": "rsi", "operator": "<", "value": 40, "points": 20,
             "category": "momentum", "label": "RSI em zona de sobrevenda"},
            {"indicator": "rsi", "operator": "<", "value": 30, "points": 10,
             "category": "momentum", "label": "RSI sobrevenda extrema (bônus)"},
            {"indicator": "stoch_k", "operator": "<", "value": 30, "points": 15,
             "category": "momentum", "label": "Stochastic em sobrevenda"},

            # Market Structure — precisa de alguma tendência para reverter
            {"indicator": "adx", "operator": ">", "value": 15, "points": 10,
             "category": "market_structure", "label": "ADX mínimo para reversão válida"},
            {"indicator": "adx", "operator": "<", "value": 30, "points": 5,
             "category": "market_structure", "label": "ADX não muito forte (confirma lateralidade)"},

            # Signal — preço próximo de suporte
            {"indicator": "bb_position", "operator": "<", "value": 0.2, "points": 15,
             "category": "signal", "label": "Preço na banda inferior de Bollinger"},
            {"indicator": "zscore", "operator": "<", "value": -1.0, "points": 10,
             "category": "signal", "label": "Z-Score negativo (desvio da média)"},

            # Liquidity
            {"indicator": "volume_spike", "operator": ">", "value": 1.2, "points": 10,
             "category": "liquidity", "label": "Volume acima da média"},
            {"indicator": "volume_24h", "operator": ">", "value": 500000, "points": 5,
             "category": "liquidity", "label": "Volume 24h mínimo"},

            # Penalties — evitar armadilhas
            {"indicator": "rsi", "operator": ">", "value": 70, "points": -15,
             "category": "momentum", "label": "RSI alto penaliza mean reversion"},
            {"indicator": "adx", "operator": ">", "value": 40, "points": -10,
             "category": "market_structure", "label": "Tendência forte demais para reversão"},
        ],
        "block_rules": [
            {"name": "Liquidez Insuficiente", "indicator": "volume_24h",
             "operator": "<", "value": 300000, "block_type": "risk",
             "reason": "Volume muito baixo para execução segura"},
            {"name": "Spread Proibitivo", "indicator": "spread_pct",
             "operator": ">", "value": 0.5, "block_type": "risk",
             "reason": "Spread alto demais — custo de entrada proibitivo"},
        ],
    },

    # ── TREND FOLLOWING ───────────────────────────────────────────────────
    "trend_following": {
        "name": "Trend Following",
        "description": (
            "Segue tendências estabelecidas. "
            "RSI alto é POSITIVO (confirma momentum). Não penaliza RSI > 60."
        ),
        "regime_affinity": [MarketRegime.TRENDING_BULL],
        "scoring_thresholds": {"strong_buy": 70, "buy": 50, "neutral": 30},
        "scoring_rules": [
            # Market Structure — tendência confirmada
            {"indicator": "adx", "operator": ">", "value": 25, "points": 20,
             "category": "market_structure", "label": "Tendência forte (ADX > 25)"},
            {"indicator": "adx", "operator": ">", "value": 35, "points": 10,
             "category": "market_structure", "label": "Tendência muito forte (bônus)"},
            {"indicator": "ema_full_alignment", "operator": "=", "value": True, "points": 20,
             "category": "market_structure", "label": "EMAs alinhadas (20 > 50 > 200)"},

            # Momentum — RSI alto é BOM em tendências
            {"indicator": "macd_value", "operator": ">", "value": 0, "points": 15,
             "category": "momentum", "label": "MACD positivo"},
            {"indicator": "macd_histogram", "operator": ">", "value": 0, "points": 10,
             "category": "momentum", "label": "MACD Histogram positivo (aceleração)"},
            {"indicator": "rsi", "operator": ">", "value": 50, "points": 10,
             "category": "momentum", "label": "RSI acima de 50 (confirma força)"},
            {"indicator": "rsi", "operator": ">", "value": 60, "points": 5,
             "category": "momentum", "label": "RSI forte — positivo em trend following"},

            # Liquidity — volume confirma tendência
            {"indicator": "volume_spike", "operator": ">", "value": 1.3, "points": 15,
             "category": "liquidity", "label": "Volume crescente confirma tendência"},
            {"indicator": "volume_24h", "operator": ">", "value": 800000, "points": 5,
             "category": "liquidity", "label": "Volume 24h adequado"},

            # Penalties
            {"indicator": "rsi", "operator": ">", "value": 85, "points": -10,
             "category": "momentum", "label": "RSI exaustão extrema (> 85)"},
            {"indicator": "adx", "operator": "<", "value": 15, "points": -20,
             "category": "market_structure", "label": "Sem tendência — invalida trend following"},
            {"indicator": "macd_value", "operator": "<", "value": 0, "points": -15,
             "category": "momentum", "label": "MACD negativo contradiz tendência de alta"},
        ],
        "block_rules": [
            {"name": "Liquidez Mínima", "indicator": "volume_24h",
             "operator": "<", "value": 500000, "block_type": "risk",
             "reason": "Volume insuficiente para trend following"},
            {"name": "Contra-tendência Completa", "indicator": "ema_full_alignment",
             "operator": "=", "value": "bearish", "block_type": "risk",
             "reason": "EMAs bearish — contra a estratégia de trend following long"},
        ],
    },

    # ── BREAKOUT HUNTER ───────────────────────────────────────────────────
    "breakout_hunter": {
        "name": "Breakout Hunter",
        "description": (
            "Captura rompimentos. Volume e ATR são mais importantes que RSI. "
            "RSI até 80 é aceitável em breakouts explosivos."
        ),
        "regime_affinity": [MarketRegime.BREAKOUT],
        "scoring_thresholds": {"strong_buy": 65, "buy": 45, "neutral": 25},
        "scoring_rules": [
            # Liquidity — volume é o sinal principal
            {"indicator": "volume_spike", "operator": ">", "value": 2.0, "points": 25,
             "category": "liquidity", "label": "Volume > 2x média (confirmação de breakout)"},
            {"indicator": "volume_spike", "operator": ">", "value": 3.0, "points": 10,
             "category": "liquidity", "label": "Volume explosivo (> 3x)"},
            {"indicator": "volume_24h", "operator": ">", "value": 1000000, "points": 5,
             "category": "liquidity", "label": "Volume 24h forte"},

            # Market Structure — tendência emergente
            {"indicator": "adx", "operator": ">", "value": 20, "points": 15,
             "category": "market_structure", "label": "Tendência emergente"},

            # Momentum — aceleração
            {"indicator": "macd_histogram", "operator": ">", "value": 0, "points": 15,
             "category": "momentum", "label": "MACD acelerando"},
            {"indicator": "rsi", "operator": "<", "value": 80, "points": 10,
             "category": "momentum", "label": "RSI não extremo (< 80)"},

            # Signal — volatilidade expandindo
            {"indicator": "atr_pct", "operator": ">", "value": 2.0, "points": 10,
             "category": "signal", "label": "Volatilidade expandindo (ATR%)"},
            {"indicator": "bb_width", "operator": ">", "value": 0.05, "points": 5,
             "category": "signal", "label": "Bollinger Bands expandindo"},

            # Penalties
            {"indicator": "volume_spike", "operator": "<", "value": 1.0, "points": -20,
             "category": "liquidity", "label": "Sem volume — breakout falso"},
            {"indicator": "adx", "operator": "<", "value": 10, "points": -15,
             "category": "market_structure", "label": "Sem força direcional"},
        ],
        "block_rules": [
            {"name": "Volume Insuficiente", "indicator": "volume_24h",
             "operator": "<", "value": 500000, "block_type": "risk",
             "reason": "Volume insuficiente para breakout legítimo"},
        ],
    },

    # ── SCALPING ──────────────────────────────────────────────────────────
    "scalping": {
        "name": "Scalping",
        "description": (
            "Movimentos curtos e rápidos. "
            "Prioriza spread baixo, volume alto e momentum imediato."
        ),
        "regime_affinity": [MarketRegime.HIGH_VOLATILITY],
        "scoring_thresholds": {"strong_buy": 75, "buy": 55, "neutral": 35},
        "scoring_rules": [
            # Liquidity — spread e profundidade são críticos
            {"indicator": "spread_pct", "operator": "<", "value": 0.05, "points": 20,
             "category": "liquidity", "label": "Spread apertado (< 0.05%)"},
            {"indicator": "spread_pct", "operator": "<", "value": 0.1, "points": 10,
             "category": "liquidity", "label": "Spread aceitável (< 0.1%)"},
            {"indicator": "volume_24h", "operator": ">", "value": 2000000, "points": 15,
             "category": "liquidity", "label": "Volume 24h alto"},
            {"indicator": "orderbook_depth_usdt", "operator": ">", "value": 50000, "points": 10,
             "category": "liquidity", "label": "Profundidade do orderbook"},

            # Signal — momentum imediato
            {"indicator": "taker_ratio", "operator": ">", "value": 0.52, "points": 15,
             "category": "signal", "label": "Pressão compradora (taker ratio)"},
            {"indicator": "stoch_k", "operator": "<", "value": 35, "points": 10,
             "category": "momentum", "label": "Stochastic em sobrevenda para entrada"},

            # Signal — volatilidade adequada (nem demais, nem de menos)
            {"indicator": "atr_pct", "operator": ">", "value": 0.5, "points": 10,
             "category": "signal", "label": "Volatilidade mínima para scalping"},
            {"indicator": "atr_pct", "operator": "<", "value": 6.0, "points": 5,
             "category": "signal", "label": "Volatilidade não extrema"},

            # Penalties
            {"indicator": "spread_pct", "operator": ">", "value": 0.2, "points": -25,
             "category": "liquidity", "label": "Spread alto — inviável para scalping"},
            {"indicator": "volume_24h", "operator": "<", "value": 500000, "points": -15,
             "category": "liquidity", "label": "Volume baixo para scalping"},
        ],
        "block_rules": [
            {"name": "Spread Proibitivo", "indicator": "spread_pct",
             "operator": ">", "value": 0.3, "block_type": "risk",
             "reason": "Spread inviável para scalping"},
            {"name": "Volume Mínimo", "indicator": "volume_24h",
             "operator": "<", "value": 1000000, "block_type": "risk",
             "reason": "Volume insuficiente para execução rápida"},
        ],
    },

    # ── SWING TRADING ─────────────────────────────────────────────────────
    "swing_trading": {
        "name": "Swing Trading",
        "description": (
            "Movimentos de vários dias. "
            "Tendência macro deve estar alinhada. Tolera pullbacks."
        ),
        "regime_affinity": [MarketRegime.LOW_VOLATILITY, MarketRegime.TRENDING_BULL],
        "scoring_thresholds": {"strong_buy": 70, "buy": 50, "neutral": 30},
        "scoring_rules": [
            # Market Structure — tendência de médio prazo
            {"indicator": "ema_full_alignment", "operator": "=", "value": True, "points": 20,
             "category": "market_structure", "label": "EMAs alinhadas para swing"},
            {"indicator": "adx", "operator": ">", "value": 20, "points": 15,
             "category": "market_structure", "label": "Tendência presente"},

            # Momentum — zona favorável
            {"indicator": "rsi", "operator": ">", "value": 40, "points": 10,
             "category": "momentum", "label": "RSI acima do neutro"},
            {"indicator": "rsi", "operator": "<", "value": 65, "points": 10,
             "category": "momentum", "label": "RSI não sobrecomprado"},
            {"indicator": "macd_value", "operator": ">", "value": 0, "points": 10,
             "category": "momentum", "label": "MACD positivo"},

            # Liquidity
            {"indicator": "volume_24h", "operator": ">", "value": 500000, "points": 10,
             "category": "liquidity", "label": "Volume 24h adequado"},

            # Macro — BTC favorável (via macro regime)
            {"indicator": "macro_allows_long", "operator": "=", "value": True, "points": 15,
             "category": "signal", "label": "Macro favorável (BTC bullish)"},

            # Penalties
            {"indicator": "rsi", "operator": ">", "value": 80, "points": -15,
             "category": "momentum", "label": "RSI sobrecomprado extremo"},
            {"indicator": "adx", "operator": "<", "value": 12, "points": -10,
             "category": "market_structure", "label": "Sem tendência para swing"},
        ],
        "block_rules": [
            {"name": "Volume Mínimo", "indicator": "volume_24h",
             "operator": "<", "value": 300000, "block_type": "risk",
             "reason": "Volume muito baixo para swing de vários dias"},
        ],
    },
}


# ── Skill Management ─────────────────────────────────────────────────────────

def get_skill_template(skill_key: str) -> Optional[SkillProfile]:
    """Returns a SkillProfile from built-in templates."""
    template = SKILL_TEMPLATES.get(skill_key)
    if not template:
        return None
    return SkillProfile(
        skill_key=skill_key,
        name=template["name"],
        description=template.get("description", ""),
        regime_affinity=template.get("regime_affinity", []),
        scoring_rules=deepcopy(template.get("scoring_rules", [])),
        scoring_thresholds=dict(template.get("scoring_thresholds", {
            "strong_buy": 80, "buy": 60, "neutral": 40,
        })),
        block_rules=deepcopy(template.get("block_rules", [])),
    )


def get_all_skill_templates() -> Dict[str, SkillProfile]:
    """Returns all built-in skill templates."""
    return {k: get_skill_template(k) for k in SKILL_TEMPLATES if get_skill_template(k)}


async def load_user_skills(db: AsyncSession, user_id: str) -> Dict[str, SkillProfile]:
    """
    Loads user's skill profiles from DB.
    Falls back to system templates if no user customization exists.
    """
    try:
        result = await db.execute(text("""
            SELECT id, skill_key, name, description, config,
                   regime_affinity, performance_history, is_active
            FROM skill_profiles
            WHERE user_id = CAST(:uid AS uuid)
              AND is_active = true
            ORDER BY skill_key
        """), {"uid": str(user_id)})
        rows = result.fetchall()

        if not rows:
            # No user skills → return system defaults
            logger.info("[SkillProfiles] No user skills for %s, using templates", user_id)
            return get_all_skill_templates()

        skills = {}
        for row in rows:
            config = row.config or {}
            affinity_raw = row.regime_affinity or []
            skills[row.skill_key] = SkillProfile(
                skill_key=row.skill_key,
                name=row.name,
                description=row.description or "",
                regime_affinity=[MarketRegime.from_string(r) for r in affinity_raw],
                scoring_rules=config.get("scoring_rules", []),
                scoring_thresholds=config.get("scoring_thresholds", {
                    "strong_buy": 80, "buy": 60, "neutral": 40,
                }),
                block_rules=config.get("block_rules", []),
                performance_history=row.performance_history or {},
                is_active=row.is_active,
                db_id=str(row.id),
            )
        return skills
    except Exception as exc:
        logger.warning("[SkillProfiles] DB load failed, using templates: %s", exc)
        return get_all_skill_templates()


async def seed_user_skills(db: AsyncSession, user_id: str) -> int:
    """
    Seeds the default skill templates for a user.
    Returns the number of skills created.
    """
    count = 0
    for skill_key, template in SKILL_TEMPLATES.items():
        try:
            # Check if exists
            existing = await db.execute(text("""
                SELECT id FROM skill_profiles
                WHERE user_id = CAST(:uid AS uuid)
                  AND skill_key = :key
                  AND is_active = true
            """), {"uid": str(user_id), "key": skill_key})
            if existing.fetchone():
                continue

            config = {
                "scoring_rules": template.get("scoring_rules", []),
                "scoring_thresholds": template.get("scoring_thresholds", {}),
                "block_rules": template.get("block_rules", []),
            }
            affinity = [
                r.value for r in template.get("regime_affinity", [])
            ]

            await db.execute(text("""
                INSERT INTO skill_profiles (
                    user_id, skill_key, name, description,
                    config, regime_affinity, is_active, is_default
                ) VALUES (
                    CAST(:uid AS uuid), :key, :name, :desc,
                    :config, :affinity, true, true
                )
            """), {
                "uid": str(user_id),
                "key": skill_key,
                "name": template["name"],
                "desc": template.get("description", ""),
                "config": json.dumps(config),
                "affinity": json.dumps(affinity),
            })
            count += 1
        except Exception as exc:
            logger.warning("[SkillProfiles] Failed to seed %s: %s", skill_key, exc)

    if count > 0:
        await db.commit()
        logger.info("[SkillProfiles] Seeded %d skills for user %s", count, user_id)
    return count
