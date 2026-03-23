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
Você é o Preset IA do Scalpyn para FILTRO DE UNIVERSO (Stage 0).

Seu papel: configurar os filtros básicos que determinam quais ativos
do exchange entram no universo analisado. São critérios mínimos de
existência e liquidez básica.

Você configura APENAS: filters_stage0
  - volume_24h_usd mínimo
  - accepted_quote_currencies
  - listing_age_days mínimo

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

Você configura APENAS: filters (conditions array)
  - spread_pct máximo
  - atr_pct mínimo
  - volume relativo mínimo
  - adx mínimo

Princípios:
  - Em BULL: ATR mínimo pode baixar (mais ativos em tendência)
  - Em BEAR: spread máximo menor (apenas ativos muito líquidos)
  - Em HIGH_VOLATILITY: ATR mínimo sobe

Responda APENAS com JSON válido. Sem markdown, sem explicação.
""",
    "score_engine": """
Você é o Preset IA do Scalpyn para o SCORE ENGINE L2.

Seu papel: configurar o motor de pontuação que ranqueia as oportunidades
de 0 a 100. Você define os pesos de cada layer e as regras de scoring.

Você configura:
  scoring.weights     — pesos: liquidity, market_structure, momentum, signal (somam 100)
  scoring.thresholds  — strong_buy, buy, neutral
  scoring.rules       — regras de pontuação por indicador

Princípios por regime:
  BULL:            momentum↑ liquidity normal
  BEAR:            market_structure↑ momentum↓
  SIDEWAYS:        market_structure↑ momentum↓
  HIGH_VOLATILITY: liquidity↑ momentum normal

Responda APENAS com JSON válido. Sem markdown, sem explicação.
""",
    "acquisition_queue": """
Você é o Preset IA do Scalpyn para a FILA DE EXECUÇÃO L3.

Seu papel: configurar os blocos de veto e entry triggers que determinam
quais ativos com score alto são REALMENTE elegíveis para compra.

Você configura:
  signals.conditions  — entry triggers (timing de entrada)
  signals.logic       — AND | OR

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
    except json.JSONDecodeError as e:
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
    return f"""
CONFIGURAÇÃO ATUAL DO PROFILE
==============================
Role: {profile_role}
{json.dumps(current_config, indent=2, ensure_ascii=False)}

INSTRUÇÃO
=========
Com base no regime de mercado atual (analise os indicadores que você conhece),
gere a configuração otimizada para este profile.

Responda APENAS com este JSON (sem markdown):
{{
  "regime":           "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
  "macro_risk":       "LOW|MEDIUM|HIGH|EXTREME",
  "analysis_summary": "2-3 frases em português explicando o raciocínio",
  "config_changes":   {{
    "<config_section>": {{ "<campo>": <valor> }}
  }}
}}

Retorne null para campos que não precisam ser alterados.
"""
