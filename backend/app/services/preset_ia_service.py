"""
preset_ia_service.py
--------------------
Preset IA: calls Claude with role-specific system prompts to configure
strategy layers automatically based on current market conditions.

Roles:
  universe_filter  → configures filters (basic universe gate)
  primary_filter   → configures filters (quality L1 gate)
  score_engine     → configures score weights + scoring rules
  acquisition_queue → configures blocks + entry triggers + risk
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── System prompts per role ───────────────────────────────────────────────────

ROLE_SYSTEM_PROMPTS = {
    "universe_filter": """
Você é o Preset IA do Scalpyn para FILTRO DE UNIVERSO (Stage 0 - POOL).

Seu papel: configurar os filtros básicos que determinam quais ativos
do exchange entram no universo analisado.

Você deve retornar "config_changes.filters.conditions" como array de objetos:
  [
    { "field": "volume_24h", "operator": ">", "value": 10000000 },
    { "field": "listing_age_days", "operator": ">", "value": 30 },
    { "field": "quote_currency", "operator": "in", "value": ["USDT", "USDC"] }
  ]

Campos disponíveis: volume_24h, listing_age_days, quote_currency, market_cap

Princípios:
  - Em BULL: relaxar volume mínimo (mais ativos entram)
  - Em BEAR: elevar volume mínimo (apenas blue chips)
  - Em EXTREME: apenas top ativos por volume

Responda APENAS com JSON válido. Sem markdown, sem explicação.
""",
    "primary_filter": """
Você é o Preset IA do Scalpyn para FILTRO PRIMÁRIO L1.

Seu papel: configurar os filtros de qualidade que eliminam ativos
sem condições adequadas de trading ANTES de calcular o score.

Você deve retornar "config_changes.filters.conditions" como array de objetos:
  [
    { "field": "spread_pct", "operator": "<", "value": 0.5 },
    { "field": "atr_pct", "operator": ">", "value": 0.5 },
    { "field": "volume_24h", "operator": ">", "value": 5000 },
    { "field": "adx", "operator": ">", "value": 20 }
  ]

E "config_changes.signals.conditions" para sinais:
  [
    { "field": "rsi", "operator": "<", "value": 70 }
  ]

Campos disponíveis: spread_pct, atr_pct, volume_24h, adx, rsi, macd, bollinger_width, change_24h

Princípios:
  - Em BULL: ATR mínimo pode baixar (mais ativos em tendência)
  - Em BEAR: spread máximo menor (apenas ativos muito líquidos)
  - Em HIGH_VOLATILITY: ATR mínimo sobe

Responda APENAS com JSON válido. Sem markdown, sem explicação.
""",
    "score_engine": """
Você é o Preset IA do Scalpyn para o SCORE ENGINE L2.

Seu papel: configurar o motor de pontuação que ranqueia as oportunidades
de 0 a 100. Você define os pesos de cada layer.

Você deve retornar "config_changes.scoring.weights" como objeto:
  {
    "liquidity": 25,
    "market_structure": 30,
    "momentum": 30,
    "signal": 15
  }
  (os valores DEVEM somar 100)

E "config_changes.filters.conditions" para critérios de seleção:
  [
    { "field": "volume_24h", "operator": ">", "value": 5000 }
  ]

Princípios por regime:
  BULL:            momentum↑ liquidity normal
  BEAR:            market_structure↑ momentum↓
  SIDEWAYS:        market_structure↑ momentum↓
  HIGH_VOLATILITY: liquidity↑ momentum normal

Responda APENAS com JSON válido. Sem markdown, sem explicação.
""",
    "acquisition_queue": """
Você é o Preset IA do Scalpyn para a FILA DE EXECUÇÃO L3.

Seu papel: configurar os entry triggers que determinam
quais ativos com score alto são REALMENTE elegíveis para compra.

Você deve retornar "config_changes.signals.conditions" como array de objetos:
  [
    { "field": "rsi", "operator": "<", "value": 30 },
    { "field": "macd_histogram", "operator": ">", "value": 0 },
    { "field": "price_vs_ema20", "operator": "<", "value": 0.02 }
  ]

Campos disponíveis: rsi, macd, macd_histogram, price_vs_ema20, price_vs_vwap, volume_ratio

E "config_changes.signals.logic" como "AND" ou "OR"

Risk parameters por regime:
  BULL:    condições mais relaxadas, mais entradas
  BEAR:    condições mais restritivas, menos entradas
  EXTREME: apenas os sinais mais fortes

Responda APENAS com JSON válido. Sem markdown, sem explicação.
""",
}

# ── Async service function ────────────────────────────────────────────────────

async def run_preset_ia(
    profile_id: str,
    profile_role: str,
    user_id: UUID,
    current_config: dict,
    db: AsyncSession,
) -> dict:
    """
    Executes Preset IA for a profile.

    Returns:
        {
          "regime":           str,
          "macro_risk":       str,
          "analysis_summary": str,
          "config_changes":   dict,
          "applied_configs":  list[str],
          "executed_at":      str,
        }
    """
    from .ai_keys_service import get_anthropic_client

    try:
        client = await get_anthropic_client(db, user_id)
    except (ValueError, ImportError) as e:
        raise ValueError(f"Anthropic não configurado: {e}")

    system_prompt = ROLE_SYSTEM_PROMPTS.get(profile_role, ROLE_SYSTEM_PROMPTS["primary_filter"])
    user_prompt = _build_user_prompt(profile_role, current_config)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text.strip()
        logger.info(
            f"[PresetIA] Response received | profile={profile_id} role={profile_role} "
            f"tokens={message.usage.input_tokens + message.usage.output_tokens}"
        )
    except Exception as e:
        logger.error(f"[PresetIA] Claude call failed: {e}")
        raise

    # Parse JSON — strip markdown fences if present
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        logger.info(f"[PresetIA] Parsed JSON successfully | role={profile_role} | config_changes keys={list(result.get('config_changes', {}).keys())}")
        logger.info(f"[PresetIA] Full config_changes: {json.dumps(result.get('config_changes', {}), indent=2)}")
    except json.JSONDecodeError as e:
        logger.error(f"[PresetIA] JSON decode failed | raw[:500]={raw[:500]}")
        raise ValueError(f"Claude retornou JSON inválido: {e}\nRaw: {raw[:500]}")

    return {
        "regime":           result.get("regime", "UNKNOWN"),
        "macro_risk":       result.get("macro_risk", "MEDIUM"),
        "analysis_summary": result.get("analysis_summary", ""),
        "config_changes":   result.get("config_changes", {}),
        "applied_configs":  list(result.get("config_changes", {}).keys()),
        "profile_role":     profile_role,
        "executed_at":      datetime.now(timezone.utc).isoformat(),
    }


def _build_user_prompt(profile_role: str, current_config: dict) -> str:
    # Define expected output structure based on role
    if profile_role in ["universe_filter", "primary_filter"]:
        expected_output = """{
  "regime":           "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
  "macro_risk":       "LOW|MEDIUM|HIGH|EXTREME",
  "analysis_summary": "2-3 frases em português explicando o raciocínio",
  "config_changes":   {
    "filters": {
      "logic": "AND",
      "conditions": [
        { "field": "volume_24h", "operator": ">", "value": 5000 },
        { "field": "atr_pct", "operator": ">", "value": 0.5 }
      ]
    },
    "signals": {
      "logic": "AND",
      "conditions": [
        { "field": "rsi", "operator": "<", "value": 70 }
      ]
    }
  }
}"""
    elif profile_role == "score_engine":
        expected_output = """{
  "regime":           "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
  "macro_risk":       "LOW|MEDIUM|HIGH|EXTREME",
  "analysis_summary": "2-3 frases em português explicando o raciocínio",
  "config_changes":   {
    "scoring": {
      "enabled": true,
      "weights": {
        "liquidity": 25,
        "market_structure": 30,
        "momentum": 30,
        "signal": 15
      }
    },
    "filters": {
      "logic": "AND",
      "conditions": [
        { "field": "volume_24h", "operator": ">", "value": 5000 }
      ]
    }
  }
}"""
    else:  # acquisition_queue / L3
        expected_output = """{
  "regime":           "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
  "macro_risk":       "LOW|MEDIUM|HIGH|EXTREME",
  "analysis_summary": "2-3 frases em português explicando o raciocínio",
  "config_changes":   {
    "signals": {
      "logic": "AND",
      "conditions": [
        { "field": "rsi", "operator": "<", "value": 30 },
        { "field": "macd_histogram", "operator": ">", "value": 0 }
      ]
    }
  }
}"""

    return f"""
CONFIGURAÇÃO ATUAL DO PROFILE
==============================
Role: {profile_role}
{json.dumps(current_config, indent=2, ensure_ascii=False)}

INSTRUÇÃO
=========
Com base no regime de mercado atual (analise os indicadores que você conhece),
gere a configuração otimizada para este profile.

IMPORTANTE: Retorne config_changes EXATAMENTE neste formato:
{expected_output}

Adapte os valores baseado no regime de mercado detectado.
Retorne null para seções que não precisam ser alteradas.
"""


# ── Integração com estrutura existente de Filters/Scoring/Signals ────────────

async def apply_preset_to_profile_builder(
    profile_id: str,
    profile_role: str,
    config_changes: dict,
    user_id: str,
    db=None,
) -> list:
    """
    Aplica as mudanças do Preset IA na estrutura existente do ProfileBuilder.
    """
    applied = []
    role_mapping = {
        'universe_filter':    ['filters'],
        'primary_filter':     ['filters'],
        'score_engine':       ['score'],
        'acquisition_queue':  ['blocks', 'risk'],
    }
    target_configs = role_mapping.get(profile_role, ['filters'])

    for config_type in target_configs:
        changes = config_changes.get(config_type) or config_changes.get(f'{config_type}s')
        if not changes:
            for key in [config_type, f'{config_type}_stage0', f'{config_type}_stage1',
                        'filters_stage0', 'filters_stage1']:
                if key in config_changes:
                    changes = config_changes[key]
                    break
        if changes:
            non_null = {k: v for k, v in changes.items() if v is not None}
            if non_null:
                applied.append(config_type)
    return applied
