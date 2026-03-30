"""
preset_ia_service.py
--------------------
Preset IA — configura automaticamente um Strategy Profile.

Fluxo:
  1. Recebe profile_id + profile_role
  2. Carrega config atual do profile
  3. Coleta snapshot de mercado
  4. Chama Claude com system prompt específico por role
  5. Claude retorna condições no formato exato do ProfileBuilder
  6. Salva via PUT /api/profiles/{id} (mesmo endpoint do Save Profile)

Formato de saída do Claude (obrigatório):
  {
    "regime":           "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
    "macro_risk":       "LOW|MEDIUM|HIGH|EXTREME",
    "analysis_summary": "resumo em português",
    "config": {
      "filters": {
        "logic": "AND",
        "conditions": [
          { "id": "cond_1", "field": "volume_24h", "operator": ">", "value": 1000000 }
        ]
      },
      "scoring": {
        "enabled": true,
        "weights": {
          "liquidity": 25,
          "market_structure": 25,
          "momentum": 25,
          "signal": 25
        }
      },
      "signals": {
        "logic": "AND",
        "conditions": [
          { "id": "sig_1", "field": "rsi", "operator": "<", "value": 45, "required": true }
        ]
      }
    }
  }

Campos de condition disponíveis (field):
  Price & Volume: volume_24h, market_cap, price, change_24h
  Momentum:       rsi, macd, macd_histogram, stoch_k, stoch_d, zscore
  Trend:          adx, bb_width, atr, atr_percent, di_plus, di_minus
  EMA:            ema_full_alignment (is_true/is_false)
  Funding:        funding_rate

Operadores disponíveis:
  >, >=, <, <=, ==, !=, between (usa min+max), in, not_in, is_true, is_false
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── System prompts por role ───────────────────────────────────────────────────

_BASE_RULES = """
REGRAS OBRIGATÓRIAS:
1. Responda APENAS com JSON válido. Sem markdown, sem texto antes ou depois.
2. IDs devem ser únicos: use "cond_1", "cond_2" para filters; "sig_1", "sig_2" para signals; "block_1", "block_2" para block_rules; "entry_1", "entry_2" para entry_triggers.
3. Weights em scoring devem somar EXATAMENTE 100.
4. Use APENAS os campos (field/indicator) e operadores listados abaixo.
5. Sempre inclua TODAS as seções no JSON: filters, scoring, signals, block_rules, entry_triggers.

CAMPOS DISPONÍVEIS (field / indicator):
  volume_24h, market_cap, price, change_24h,
  rsi, macd, macd_histogram, stoch_k, stoch_d, zscore,
  adx, bb_width, atr, atr_percent, di_plus, di_minus,
  ema_full_alignment, ema9_gt_ema50, ema50_gt_ema200, funding_rate,
  alpha_score, volume_spike

OPERADORES filters/signals: >, >=, <, <=, ==, !=, between, in, not_in, is_true, is_false
  (para "between": use "min" e "max" no lugar de "value")
  (para "is_true"/"is_false": não use "value")
OPERADORES block_rules/entry_triggers: >, <, >=, <=

REGRAS DE LAYER:
  POOL / L1  → filters + block_rules (sem entry_triggers)
  L2         → filters + scoring + signals + block_rules (sem entry_triggers)
  L3         → block_rules + signals + entry_triggers (sem filters)
"""

ROLE_PROMPTS = {

    'universe_filter': f"""
Você é o Preset IA do Scalpyn configurando um FILTRO DE UNIVERSO (POOL — Stage 0).

Seu papel: definir filtros básicos que determinam quais ativos da corretora
entram no universo analisado. São critérios mínimos de liquidez e existência.

FILTERS: volume_24h mínimo, market_cap mínimo, change_24h para excluir colapsos extremos.
SCORING: deixe weights em 25/25/25/25 (neutro para POOL).
SIGNALS: deixe vazio [] para POOL.
BLOCK_RULES: veto de ativos com spread abusivo ou funding_rate extremo.
ENTRY_TRIGGERS: deixe vazio [] para POOL (sem execução neste layer).

Regime BULL:     volume_24h > 500k, market_cap > 10M
Regime BEAR:     volume_24h > 2M,   market_cap > 50M
Regime SIDEWAYS: volume_24h > 1M,   market_cap > 20M
Regime EXTREME:  volume_24h > 5M,   market_cap > 100M

{_BASE_RULES}
""",

    'primary_filter': f"""
Você é o Preset IA do Scalpyn configurando um FILTRO PRIMÁRIO L1 (Stage 1).

Seu papel: filtrar ativos com qualidade técnica inadequada para trading.

FILTERS: atr_percent mínimo, adx mínimo, volume_24h operacional, rsi para excluir extremos.
SCORING: weights neutros 25/25/25/25 (L1 não rankeia, apenas filtra).
SIGNALS: ema_full_alignment, di_plus/di_minus para confirmar direção.
BLOCK_RULES: bloquear ativos em rsi extremo (>85 overbought, <15 oversold), adx < 10 (sem tendência).
ENTRY_TRIGGERS: deixe vazio [] para L1.

Regime BULL:            atr_percent > 1.5, adx > 18, block rsi > 85
Regime BEAR:            atr_percent > 2.0, adx > 22, block rsi > 80
Regime SIDEWAYS:        atr_percent > 1.0, adx > 15
Regime HIGH_VOLATILITY: atr_percent > 3.0, adx > 25, block rsi > 78

{_BASE_RULES}
""",

    'score_engine': f"""
Você é o Preset IA do Scalpyn configurando o SCORE ENGINE L2 (Stage 2).

Seu papel: definir os PESOS que ranqueiam as oportunidades de 0-100.

FILTERS: condições mínimas para entrar no cálculo de score.
SCORING: weights que somam 100 (liquidity, market_structure, momentum, signal).
SIGNALS: condições de sinal obrigatórias para confirmar qualidade do ativo.
BLOCK_RULES: veto de ativos com rsi overbought, spread alto ou sem tendência.
ENTRY_TRIGGERS: deixe vazio [] para L2 (sem execução neste layer).

Regime BULL:            momentum: 35, market_structure: 30, signal: 20, liquidity: 15; block rsi > 82
Regime BEAR:            market_structure: 35, momentum: 30, liquidity: 20, signal: 15; block rsi > 75
Regime SIDEWAYS:        market_structure: 35, momentum: 25, signal: 25, liquidity: 15; block adx < 12
Regime HIGH_VOLATILITY: momentum: 40, liquidity: 30, market_structure: 20, signal: 10; block spread_pct > 1.5

{_BASE_RULES}
""",

    'acquisition_queue': f"""
Você é o Preset IA do Scalpyn configurando a FILA DE EXECUÇÃO L3 (Stage 3).

Seu papel: definir as condições FINAIS de veto (block_rules) e entrada (entry_triggers).
Apenas ativos que passaram L1 e L2 chegam aqui. São as últimas condições antes da execução.

FILTERS: deixe vazio [] para L3 (filtros já foram feitos em L1/L2).
SCORING: weights neutros 25/25/25/25.
SIGNALS: contexto de momentum — rsi, macd, volume_24h.
BLOCK_RULES: veto absoluto — nunca comprar overbought, sem tendência ou spread alto.
ENTRY_TRIGGERS: timing exato de entrada — rsi na zona ideal, macd positivo, volume confirmando.

Regime BULL:
  block: rsi > 65, adx < 18
  entry: rsi < 55, macd > 0, volume_24h > 1000000, ema_full_alignment is_true

Regime BEAR:
  block: rsi > 50, adx < 25
  entry: rsi < 40, adx > 30

Regime SIDEWAYS:
  block: rsi > 65, adx < 12
  entry: rsi < 45, zscore < -1

Regime EXTREME:
  block: rsi > 40, spread_pct > 1.0
  entry: rsi < 30, volume_24h > 5000000

{_BASE_RULES}
""",
}

# ── Snapshot de mercado ───────────────────────────────────────────────────────

async def _get_market_snapshot() -> dict:
    """Coleta snapshot de mercado. Retorna dict vazio se falhar."""
    try:
        from services.market_snapshot import build_market_snapshot
        snap = await build_market_snapshot(depth='full')
        return {
            'collected_at': snap.collected_at,
            'crypto':       snap.crypto,
            'macro':        snap.macro,
            'sentiment':    snap.sentiment,
            'news':         snap.news[:5],
        }
    except Exception as e:
        logger.warning(f'[PresetIA] Falha ao coletar mercado: {e}')
        return {}


def _build_prompt(profile_role: str, current_config: dict, snapshot: dict) -> str:
    crypto    = snapshot.get('crypto', {})
    macro     = snapshot.get('macro', {})
    sentiment = snapshot.get('sentiment', {})
    news      = snapshot.get('news', [])
    btc       = crypto.get('blue_chips', {}).get('BTC', {})
    eth       = crypto.get('blue_chips', {}).get('ETH', {})

    return f"""
CONDIÇÕES DE MERCADO ATUAIS
============================
BTC:  ${btc.get('price', 'N/A')} | 24h: {btc.get('change_24h', 'N/A')}%
ETH:  ${eth.get('price', 'N/A')} | 24h: {eth.get('change_24h', 'N/A')}%
BTC Dominance:   {crypto.get('btc_dominance', 'N/A')}%
Fear & Greed:    {sentiment.get('fear_greed_value', 'N/A')} ({sentiment.get('fear_greed_label', 'N/A')})
Fear & Greed Δ:  {sentiment.get('fear_greed_delta', 'N/A')} pts
DXY:    {macro.get('DXY', {}).get('value', 'N/A')} | Δ: {macro.get('DXY', {}).get('change_pct', 'N/A')}%
S&P500: {macro.get('SP500', {}).get('value', 'N/A')} | Δ: {macro.get('SP500', {}).get('change_pct', 'N/A')}%
VIX:    {macro.get('VIX', {}).get('value', 'N/A')}
{chr(10).join([f"- {n.get('title','')}" for n in news]) or '- Sem notícias'}

CONFIGURAÇÃO ATUAL DO PROFILE
==============================
{json.dumps(current_config, indent=2, ensure_ascii=False)}

INSTRUÇÃO
=========
Analise as condições de mercado e gere a configuração COMPLETA para este profile.
Responda APENAS com o JSON no formato abaixo, sem nenhum texto adicional.
Inclua TODAS as 5 seções: filters, scoring, signals, block_rules, entry_triggers.

{{
  "regime":           "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
  "macro_risk":       "LOW|MEDIUM|HIGH|EXTREME",
  "analysis_summary": "2-3 frases em português explicando o raciocínio",
  "config": {{
    "filters": {{
      "logic": "AND",
      "conditions": [
        {{ "id": "cond_1", "field": "FIELD", "operator": "OPERATOR", "value": VALUE }}
      ]
    }},
    "scoring": {{
      "enabled": true,
      "weights": {{
        "liquidity": 25,
        "market_structure": 25,
        "momentum": 25,
        "signal": 25
      }}
    }},
    "signals": {{
      "logic": "AND",
      "conditions": [
        {{ "id": "sig_1", "field": "FIELD", "operator": "OPERATOR", "value": VALUE, "required": true }}
      ]
    }},
    "block_rules": {{
      "blocks": [
        {{ "id": "block_1", "name": "NOME DO BLOCK", "enabled": true, "indicator": "INDICATOR", "type": "threshold", "operator": "OPERATOR", "value": VALUE, "reason": "MOTIVO DO BLOQUEIO" }}
      ]
    }},
    "entry_triggers": {{
      "logic": "AND",
      "conditions": [
        {{ "id": "entry_1", "indicator": "INDICATOR", "operator": "OPERATOR", "value": VALUE, "required": true, "enabled": true }}
      ]
    }}
  }}
}}
"""


def _audit_filter_fields(conditions: list) -> list:
    """
    SCALPYN_PRESET_AUDITOR_V1 — Corrige mapeamentos de campo errados gerados pelo AI.

    Normaliza nomes de campo para o padrão do frontend (INDICATOR_FIELDS):
      change_24h_pct / price_change_24h → change_24h
      atr_pct / atr_percentage          → atr_percent

    Regras de detecção quando field = volume_24h mas valor não faz sentido:
      value < 0                → change_24h
      0 < |value| <= 5         → atr_percent
      5 < |value| <= 100       → change_24h
      |value| >= 100_000       → manter como volume_24h

    Remove condições logicamente impossíveis:
      volume_24h ou market_cap com valor negativo
      atr_percent <= 0
    """
    # Normalização de aliases de nomes de campo
    FIELD_ALIASES = {
        "change_24h_pct":    "change_24h",
        "price_change_24h":  "change_24h",
        "change_pct_24h":    "change_24h",
        "atr_pct":           "atr_percent",
        "atr_percentage":    "atr_percent",
        "bollinger_width":   "bb_width",
    }

    logger.info(f'[Auditor] Iniciando auditoria em {len(conditions)} condições: {conditions}')

    fixed = []
    for cond in conditions:
        field = cond.get("field", "")
        value = cond.get("value", 0)
        op    = cond.get("operator", ">=")

        # Normalizar aliases primeiro
        if field in FIELD_ALIASES:
            cond["field"] = FIELD_ALIASES[field]
            field = cond["field"]

        # Coerção de tipo: AI às vezes retorna valores numéricos como strings
        # Inclui tratamento de notação europeia (vírgula como separador decimal: "0,5" → 0.5)
        if isinstance(value, str):
            try:
                normalized = value.strip().replace(",", ".")
                coerced = float(normalized)
                value = coerced
                cond["value"] = coerced
            except (ValueError, TypeError):
                logger.warning(f'[Auditor] Não foi possível converter valor "{value}" para número no campo "{field}"')

        # Detectar uso incorreto de volume_24h baseado no valor
        if field == "volume_24h" and isinstance(value, (int, float)):
            abs_val = abs(value)
            if value < 0:
                cond["field"] = "change_24h"
                field = "change_24h"
            elif abs_val <= 5:
                cond["field"] = "atr_percent"
                field = "atr_percent"
            elif abs_val <= 100:
                cond["field"] = "change_24h"
                field = "change_24h"

        # Remover condições logicamente impossíveis
        if field in ("volume_24h", "market_cap"):
            if isinstance(value, (int, float)) and value < 0:
                continue

        if field == "atr_percent":
            if op in ("<=", "<") and isinstance(value, (int, float)) and value <= 0:
                continue

        fixed.append(cond)

    # Remover duplicatas exatas (mesmo field + operator + value)
    seen: set = set()
    deduped = []
    for cond in fixed:
        raw_value = cond.get("value")
        # Lists (e.g. [min, max] for "between") are not hashable — convert to tuple
        hashable_value = tuple(raw_value) if isinstance(raw_value, list) else raw_value
        key = (cond.get("field"), cond.get("operator"), hashable_value)
        if key not in seen:
            seen.add(key)
            deduped.append(cond)

    logger.info(f'[Auditor] Resultado após auditoria ({len(deduped)} condições): {deduped}')
    return deduped


def _validate_config(config: dict, profile_role: str) -> dict:
    """
    Valida e corrige o config retornado pelo Claude.
    Garante que está no formato exato do ProfileBuilder (5 seções).
    """
    ts = int(time.time() * 1000)

    # ── Garantir estrutura base das 5 seções ──────────────────────────────────
    if 'filters' not in config:
        config['filters'] = {'logic': 'AND', 'conditions': []}
    if 'scoring' not in config:
        config['scoring'] = {'enabled': True, 'weights': {'liquidity': 25, 'market_structure': 25, 'momentum': 25, 'signal': 25}}
    if 'signals' not in config:
        config['signals'] = {'logic': 'AND', 'conditions': []}
    if 'block_rules' not in config:
        config['block_rules'] = {'blocks': []}
    if 'entry_triggers' not in config:
        config['entry_triggers'] = {'logic': 'AND', 'conditions': []}

    # ── Auditar e corrigir campos de filters ──────────────────────────────────
    config['filters']['conditions'] = _audit_filter_fields(
        config['filters'].get('conditions', [])
    )

    # ── Garantir IDs e defaults nas conditions de filters ─────────────────────
    for i, cond in enumerate(config['filters'].get('conditions', [])):
        if not cond.get('id'):
            cond['id'] = f'cond_{ts}_{i}'
        cond.setdefault('field', 'volume_24h')
        cond.setdefault('operator', '>')
        cond.setdefault('value', 0)

    # ── Garantir IDs e defaults nas conditions de signals ─────────────────────
    for i, cond in enumerate(config['signals'].get('conditions', [])):
        if not cond.get('id'):
            cond['id'] = f'sig_{ts}_{i}'
        cond.setdefault('field', 'rsi')
        cond.setdefault('operator', '<')
        cond.setdefault('value', 50)
        cond.setdefault('required', False)

    # ── Garantir IDs e defaults nos blocks de block_rules ─────────────────────
    blocks = config['block_rules'].get('blocks', [])
    if not isinstance(blocks, list):
        blocks = []
        config['block_rules']['blocks'] = blocks
    for i, block in enumerate(blocks):
        if not block.get('id'):
            block['id'] = f'block_{ts}_{i}'
        block.setdefault('name', f'Block {i + 1}')
        block.setdefault('enabled', True)
        block.setdefault('indicator', 'rsi')
        block.setdefault('type', 'threshold')
        block.setdefault('operator', '>')
        block.setdefault('value', 80)
        block.setdefault('reason', '')

    # ── Garantir IDs e defaults nas conditions de entry_triggers ──────────────
    entry_conds = config['entry_triggers'].get('conditions', [])
    if not isinstance(entry_conds, list):
        entry_conds = []
        config['entry_triggers']['conditions'] = entry_conds
    config['entry_triggers'].setdefault('logic', 'AND')
    for i, trig in enumerate(entry_conds):
        if not trig.get('id'):
            trig['id'] = f'entry_{ts}_{i}'
        # entry_triggers usa "indicator" em vez de "field"
        if 'field' in trig and 'indicator' not in trig:
            trig['indicator'] = trig.pop('field')
        trig.setdefault('indicator', 'rsi')
        trig.setdefault('operator', '<')
        trig.setdefault('value', 60)
        trig.setdefault('required', False)
        trig.setdefault('enabled', True)

    # ── Validar weights somam 100 ─────────────────────────────────────────────
    weights = config['scoring'].get('weights', {})
    total   = sum(weights.values())
    if total != 100 and total > 0:
        factor = 100 / total
        config['scoring']['weights'] = {k: round(v * factor) for k, v in weights.items()}
        diff = 100 - sum(config['scoring']['weights'].values())
        if diff != 0:
            first_key = next(iter(config['scoring']['weights']))
            config['scoring']['weights'][first_key] += diff

    config['scoring']['enabled'] = True
    return config


# ── Main function ─────────────────────────────────────────────────────────────

async def run_preset_ia(
    profile_id: str,
    profile_role: str,
    user_id: str,
    current_profile_config: dict,
    db=None,
) -> dict:
    """
    Executa o Preset IA para um profile.

    Args:
        profile_id:             ID do profile
        profile_role:           role do profile (universe_filter, primary_filter, etc.)
        user_id:                ID do usuário
        current_profile_config: config atual do profile (campo config do profile)
        db:                     sessão de banco

    Returns:
        {
            'regime':           str,
            'macro_risk':       str,
            'analysis_summary': str,
            'config':           dict,  ← pronto para PUT /api/profiles/{id}
            'executed_at':      str,
        }
    """
    from .ai_keys_service import get_anthropic_client
    from ..models.ai_skill import AiSkill
    from sqlalchemy import select, and_
    import uuid as _uuid

    client = await get_anthropic_client(db=db, user_id=user_id)

    # Tentar buscar Skill ativa do usuário para este role_key
    system_prompt = None
    if db is not None:
        try:
            uid = _uuid.UUID(str(user_id))
            result = await db.execute(
                select(AiSkill).where(
                    and_(
                        AiSkill.user_id == uid,
                        AiSkill.role_key == profile_role,
                        AiSkill.is_active == True,
                    )
                ).order_by(AiSkill.updated_at.desc()).limit(1)
            )
            skill = result.scalar_one_or_none()
            if skill:
                system_prompt = skill.prompt_text
                logger.info(f'[PresetIA] Usando Skill personalizada "{skill.name}" para role={profile_role}')
        except Exception as e:
            logger.warning(f'[PresetIA] Falha ao buscar Skill do DB: {e}')

    if system_prompt is None:
        system_prompt = ROLE_PROMPTS.get(
            profile_role,
            ROLE_PROMPTS['primary_filter']
        )
        logger.info(f'[PresetIA] Usando prompt padrão para role={profile_role}')

    # Coletar mercado
    snapshot = await _get_market_snapshot()

    # Montar prompt
    user_prompt = _build_prompt(
        profile_role=profile_role,
        current_config=current_profile_config,
        snapshot=snapshot,
    )

    # Chamar Claude
    logger.info(f'[PresetIA] Chamando Claude | profile={profile_id} role={profile_role}')
    try:
        message = client.messages.create(
            model='claude-sonnet-4-5',
            max_tokens=4096,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        raw = message.content[0].text.strip()
        logger.info(
            f'[PresetIA] Resposta recebida | tokens={message.usage.input_tokens + message.usage.output_tokens}'
        )
    except Exception as e:
        logger.error(f'[PresetIA] Erro na chamada Claude: {e}')
        raise

    # Parse JSON
    try:
        clean  = raw.replace('```json', '').replace('```', '').strip()
        result = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error(f'[PresetIA] JSON inválido: {e}\nRaw: {raw[:500]}')
        raise ValueError(f'Claude retornou JSON inválido: {e}')

    # Validar e corrigir o config
    raw_config = result.get('config', {})
    validated_config = _validate_config(raw_config, profile_role)

    return {
        'regime':           result.get('regime', 'UNKNOWN'),
        'macro_risk':       result.get('macro_risk', 'MEDIUM'),
        'analysis_summary': result.get('analysis_summary', ''),
        'config':           validated_config,
        'executed_at':      datetime.now(timezone.utc).isoformat(),
    }


# ── Pool Preset IA ────────────────────────────────────────────────────────────

def _build_pool_analysis_prompt(
    pool_name: str,
    symbols: list,
    market_data: list,
    current_config: dict,
) -> str:
    symbols_str = ", ".join(symbols[:50]) if symbols else "nenhum ativo"
    total = len(symbols)
    sample_data = market_data[:10] if market_data else []
    sample_lines = "\n".join(
        f"  {d.get('symbol','?')}: ${d.get('price',0):.4f} | vol={d.get('volume_24h',0)/1e6:.1f}M | Δ24h={d.get('change_24h', d.get('change_24h_pct',0)):.1f}%"
        for d in sample_data
    ) or "  (sem dados)"
    snapshot_str = json.dumps(current_config, indent=2, ensure_ascii=False)

    return f"""
POOL: {pool_name}
Total de ativos: {total}
Amostra de ativos: {symbols_str}

DADOS DE MERCADO (top 10 por volume):
{sample_lines}

CONFIGURAÇÃO ATUAL DO POOL (overrides):
{snapshot_str}

INSTRUÇÃO
=========
Analise os ativos deste pool e as condições de mercado.
Sugira critérios de filtro e scoring para melhorar a qualidade do pool.
Responda APENAS com JSON no formato:

{{
  "regime": "BULL|BEAR|SIDEWAYS|HIGH_VOLATILITY",
  "macro_risk": "LOW|MEDIUM|HIGH|EXTREME",
  "analysis_summary": "2-3 frases em português explicando o raciocínio",
  "recommendations": {{
    "min_volume_24h": <número em USD ou null>,
    "min_market_cap": <número em USD ou null>,
    "max_assets": <número ou null>,
    "remove_symbols": [<lista de símbolos a remover, se algum>],
    "add_symbols": [<lista de símbolos a considerar adicionar, se algum>]
  }}
}}
"""


async def run_preset_ia_for_pool(
    pool_id: str,
    pool_name: str,
    symbols: list,
    user_id: str,
    current_overrides: dict,
    db=None,
) -> dict:
    """
    Executa Preset IA para um Pool.
    Analisa os ativos do pool e sugere critérios de filtro/scoring.

    Returns:
        {
            'regime':           str,
            'macro_risk':       str,
            'analysis_summary': str,
            'recommendations':  dict,
            'executed_at':      str,
        }
    """
    from .ai_keys_service import get_anthropic_client
    from .market_data_service import market_data_service

    client = await get_anthropic_client(db=db, user_id=user_id)

    # Coletar dados de mercado para os ativos do pool
    try:
        market_data = await market_data_service.get_market_metadata(symbols=symbols)
        market_data.sort(key=lambda x: x.get('volume_24h', 0), reverse=True)
    except Exception as e:
        logger.warning(f'[PoolPresetIA] Falha ao coletar market data: {e}')
        market_data = []

    # Montar prompt
    snapshot = await _get_market_snapshot()
    system_prompt = f"""
Você é o Preset IA do Scalpyn analisando um POOL de ativos para trading.

Seu papel: analisar os ativos do pool e sugerir critérios de filtro e scoring
para melhorar a qualidade e performance do pool.

{_BASE_RULES}
"""
    user_prompt = _build_pool_analysis_prompt(
        pool_name=pool_name,
        symbols=symbols,
        market_data=market_data,
        current_config=current_overrides,
    )

    logger.info(f'[PoolPresetIA] Chamando Claude | pool={pool_id} assets={len(symbols)}')
    try:
        message = client.messages.create(
            model='claude-sonnet-4-5',
            max_tokens=2048,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        raw = message.content[0].text.strip()
    except Exception as e:
        logger.error(f'[PoolPresetIA] Erro na chamada Claude: {e}')
        raise

    try:
        clean = raw.replace('```json', '').replace('```', '').strip()
        result = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error(f'[PoolPresetIA] JSON inválido: {e}\nRaw: {raw[:500]}')
        raise ValueError(f'Claude retornou JSON inválido: {e}')

    return {
        'regime':           result.get('regime', 'UNKNOWN'),
        'macro_risk':       result.get('macro_risk', 'MEDIUM'),
        'analysis_summary': result.get('analysis_summary', ''),
        'recommendations':  result.get('recommendations', {}),
        'executed_at':      datetime.now(timezone.utc).isoformat(),
    }


async def apply_pool_preset_recommendations(
    pool_id: str,
    recommendations: dict,
    db,
) -> dict:
    """
    Aplica as recomendações do Preset IA ao pool.
    Atualiza overrides e remove símbolos marcados para remoção.

    Returns: { applied_overrides, removed_count }
    """
    from sqlalchemy import select, delete
    from ..models.pool import Pool, PoolCoin

    pool_query = select(Pool).where(Pool.id == pool_id)
    result = await db.execute(pool_query)
    pool = result.scalar_one_or_none()
    if not pool:
        raise ValueError(f'Pool {pool_id} não encontrado')

    overrides = dict(pool.overrides or {})

    # Aplicar overrides recomendados
    if recommendations.get('min_volume_24h') is not None:
        overrides['min_volume_24h'] = recommendations['min_volume_24h']
    if recommendations.get('min_market_cap') is not None:
        overrides['min_market_cap'] = recommendations['min_market_cap']
    if recommendations.get('max_assets') is not None:
        overrides['max_assets'] = recommendations['max_assets']

    pool.overrides = overrides

    # Remover símbolos marcados (apenas discovered)
    removed_count = 0
    remove_symbols = recommendations.get('remove_symbols', [])
    if remove_symbols:
        coins_query = select(PoolCoin).where(
            PoolCoin.pool_id == pool_id,
            PoolCoin.symbol.in_([s.upper() for s in remove_symbols]),
            PoolCoin.origin == 'discovered',
        )
        coins_result = await db.execute(coins_query)
        coins_to_remove = coins_result.scalars().all()
        for coin in coins_to_remove:
            await db.delete(coin)
            removed_count += 1

    await db.commit()

    return {
        'applied_overrides': overrides,
        'removed_count': removed_count,
    }
