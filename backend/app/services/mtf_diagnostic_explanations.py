"""Frozen explanatory metadata only. Never changes predicates, plans or signal identity."""
from copy import deepcopy
from ..schemas.mtf_rules import DURATIONS

VERSION = "mtf_check_explanation_v1"
INDICATOR_METHODS = {
    "ema": "Média exponencial dos fechamentos, nos períodos configurados.",
    "rsi": "Compara ganhos e perdas entre fechamentos com suavização de Wilder no período configurado.",
    "adx": "Calcula movimentos direcionais, DI e força da tendência com suavização de Wilder; a saída selecionada identifica a grandeza comparada.",
    "atr": "Média móvel simples do true range: amplitude do candle e distâncias ao fechamento anterior.",
    "macd": "Diferença entre médias exponenciais rápida e lenta, linha de sinal e histograma; usa a saída selecionada.",
    "bollinger": "Média dos fechamentos e bandas por desvio padrão populacional; usa a saída selecionada.",
    "stochastic": "Posição do fechamento entre mínima e máxima da janela, com suavização de K e D.",
    "vwap": "Preço típico ponderado por volume, acumulado por dia dentro da amostra disponível.",
    "obv": "Acumula volume com o sinal da variação do fechamento dentro da amostra disponível.",
    "zscore": "Distância do fechamento à média móvel dividida pelo desvio padrão amostral.",
    "volume_spike": "Volume atual dividido pela média da janela, incluindo o candle atual.",
    "rvol_strict": "Volume atual dividido pela média dos candles anteriores, excluindo o candle atual da média.",
    "parabolic_sar": "SAR parabólico calculado com extremos e fatores de aceleração configurados; usa a saída selecionada.",
}

TITLES = {
    "btc_downtrend": "Tendência estrutural do Bitcoin",
    "rules:GATILHO": "Condições do gatilho de entrada",
    "rules:ARMADO": "Condições para armar a entrada",
    "audit:structure_alignment": "Direção da estrutura principal",
    "audit:zone_alignment": "Direção estrutural do período da zona",
    "audit:structure_integrity": "Integridade da estrutura principal",
    "audit:zone_integrity": "Integridade da estrutura do período da zona",
}
PIVOT_METHOD = (
    "Topos superam estritamente as máximas dos vizinhos e fundos ficam abaixo das mínimas. "
    "A confirmação aguarda os candles à direita. Os últimos dois topos e dois fundos "
    "ascendentes indicam Alta; descendentes, Baixa; as demais combinações, Lateral."
)
INTEGRITY_METHOD = (
    "Percorre fechamentos e pivôs já confirmados em cada instante. Em alta, fechamento abaixo "
    "do último fundo ascendente (HL) rompe a estrutura; em baixa, fechamento acima do último "
    "topo descendente (LH) a rompe. Igualdade ou apenas pavio não rompem. Voltar ao nível não "
    "recupera a integridade: é necessária nova estrutura direcional completa após o rompimento."
)
# Text and config paths describe the existing producer; no thresholds are introduced here.
NATIVE = {
    "zone": ("Distância do fechamento ao suporte selecionado, dividida pelo ATR do período da zona.",
             ("zone_proximity_atr", "zone_atr_mult", "zone_min_touches", "zone_lookback_candles")),
    "trigger": ("Verifica os padrões de compra habilitados no candle avaliado; basta um padrão completo.",
                ("trigger_patterns", "min_range_atr", "engulf_include_wicks", "pin_wick_ratio", "pin_wick_pct", "pin_close_fraction")),
    "asset_downtrend": ("Veta a classificação estrutural de baixa do ativo.", ("pivot_left", "pivot_right")),
    "asset_range": ("Veta a classificação estrutural lateral do ativo.", ("pivot_left", "pivot_right")),
    "spread_available": ("Verifica se a fonte de mercado forneceu o spread para esta avaliação.", ()),
    "spread": ("Compara o spread observado no livro com o limite percentual configurado.", ("max_spread_pct",)),
    "stop_valid": ("Verifica se o preço de stop calculado é positivo.", ("stop_buffer_atr", "stop_sizing")),
    "stop_distance": ("Compara a distância entre entrada e stop com o limite em ATR do período do gatilho.", ("max_stop_atr",)),
    "targets": ("Verifica a disponibilidade dos alvos estruturais utilizados pelo plano.", ("zone_atr_mult", "zone_min_touches")),
    "risk_valid": ("Verifica se o risco líquido calculado pelo plano é positivo após custos.", ("fee_per_side_pct", "slippage_pct")),
    "risk_reward": ("Compara retorno/risco líquido do plano com o mínimo exigido.", ("min_rr_net", "fee_per_side_pct", "slippage_pct")),
    "plan": ("Verifica se as condições anteriores permitiram calcular o plano de entrada e saída.", ()),
    "score": ("Compara a pontuação final com o mínimo. Vetos continuam obrigatórios.", ("min_score", "score_weights")),
    "score:trend": ("Pontua a estrutura de alta; isoladamente não é veto.", ("score_weights", "pivot_left", "pivot_right")),
    "score:force": ("Pontua um padrão de força habilitado no período da estrutura.", ("force_patterns", "score_weights")),
    "score:zone": ("Pontua a proximidade do suporte segundo a distância em ATR.", ("zone_proximity_atr", "score_weights")),
    "score:rejection": ("Pontua a rejeição no suporte na janela configurada.", ("rejection_patterns", "rejection_lookback", "score_weights")),
    "score:trigger": ("Pontua um padrão de gatilho habilitado.", ("trigger_patterns", "score_weights")),
    "score:rr": ("Pontua o retorno/risco líquido quando alcança o limite de bônus.", ("rr_bonus_threshold", "score_weights")),
    "score:volume": ("Divide o volume do candle pela média dos candles anteriores e compara ao multiplicador.", ("vol_lookback", "vol_mult", "score_weights")),
    "audit:lh_break": ("Quando o último topo confirmado é descendente (LH), exige fechamento acima dele. Sem topo conhecido, o dado é indisponível.",
                       ("context_audit.require_trigger_lh_break",)),
    "audit:stop_atr": ("Divide a distância entre entrada e stop pelo ATR do período do gatilho e exige a distância mínima.",
                       ("context_audit.min_stop_atr_trigger",)),
    "audit:stop_swing": ("Exige stop estritamente abaixo do último fundo operacional confirmado.",
                         ("context_audit.require_stop_beyond_trigger_swing",)),
    "audit:stop_zone": ("Exige stop estritamente abaixo do limite inferior do suporte que armou a entrada.",
                        ("context_audit.require_stop_beyond_arming_zone",)),
    "audit:reference_rr": ("Compara o retorno/risco bruto do alvo percentual de referência com o mínimo exigido.",
                           ("context_audit.min_reference_rr_gross", "stop_sizing.target_pct")),
    "audit:reference_reward": ("Exige ganho líquido positivo no alvo percentual, após taxas e slippage de entrada e saída.",
                               ("context_audit.require_positive_reference_reward_net", "stop_sizing.target_pct", "fee_per_side_pct", "slippage_pct")),
    "audit:data": ("Verifica a disponibilidade dos dados necessários à auditoria de contexto e risco.", ()),
    "evaluation_current": ("Compara a idade da avaliação com a validade permitida no período do gatilho.", ("stale_tf_multiplier",)),
}


def parameters(cfg, paths):
    result = {}
    for path in paths:
        value = cfg
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
        result[path] = deepcopy(value.model_dump() if hasattr(value, "model_dump") else value)
    return result


def window(frame, tf, cutoff, *, preview=False):
    candles = [c for c in frame.get("candles", []) if c["time"] <= cutoff
               and (preview or (c["time"] + DURATIONS[tf] <= cutoff and c.get("is_closed") is not False))]
    return dict(start_at=candles[0]["time"] if candles else None,
                end_at=candles[-1]["time"] + DURATIONS[tf] if candles else None,
                candle_count=len(candles), scope="analysis_history")


def selected_pivots(points):
    return deepcopy(sorted([p for kind in ("H", "L")
                            for p in [p for p in points if p["kind"] == kind][-2:]],
                           key=lambda p: (p["time"], p["kind"])))


def operand_detail(spec, sample, cfg, frames, cutoff, preview):
    ref = spec.get("ref")
    detail = dict(ref=ref, constant=deepcopy(spec.get("value")))
    if not ref:
        return detail
    if ref.startswith("indicators."):
        instance = (cfg.indicators or {}).get(ref.split(".", 1)[1])
        detail.update(deepcopy(sample or {}))
        # Same evaluation config, including when no valid sample exists.
        detail["configuration"] = instance.model_dump() if instance else detail.get("configuration")
        if instance:
            detail["timeframe"] = instance.timeframe
            detail["method"] = INDICATOR_METHODS.get(instance.family)
        return detail
    stage = ref.split(".", 1)[0]
    tf = (cfg.btc_trend_timeframe if stage == "btc" else
          cfg.stage("trigger") if stage == "volume" else
          cfg.stage(stage) if stage in ("structure", "zone", "trigger") else None)
    detail["timeframe"] = tf
    if tf and stage != "btc":
        detail["window"] = window(frames.get(tf, {}), tf, cutoff, preview=preview)
    paths = {
        "structure": ("pivot_left", "pivot_right", "force_patterns"),
        "zone": ("zone_proximity_atr", "zone_atr_mult", "zone_min_touches", "zone_lookback_candles", "rejection_lookback"),
        "trigger": ("trigger_patterns", "min_range_atr", "engulf_include_wicks", "pin_wick_ratio", "pin_wick_pct", "pin_close_fraction"),
        "volume": ("vol_lookback",),
        "btc": ("pivot_left", "pivot_right", "btc_trend_timeframe"),
        "flow": ("pump_flow",),
    }
    detail["parameters"] = parameters(cfg, paths.get(stage, ()))
    detail["method"] = {
        "structure": PIVOT_METHOD + " A força usa os padrões configurados.",
        "zone": "Suporte selecionado, distância em ATR e rejeição na janela configurada.",
        "trigger": "Reconhecimento dos padrões habilitados no candle avaliado.",
        "volume": "Volume do candle dividido pela média dos candles anteriores.",
        "btc": PIVOT_METHOD,
        "market": "Leitura de mercado preservada nesta avaliação.",
        "flow": "Agregação do fluxo na janela configurada; disponibilidade e cobertura pertencem à fonte.",
    }.get(stage, "Referência de contexto preservada na avaliação.")
    return detail


def explain_diagnostics(result, frames, cfg, cutoff, *, preview=False):
    diagnostic = result["trigger_diagnostics"]
    diagnostic["explanation_context"] = dict(version=VERSION, cutoff=cutoff, symbol=result["symbol"],
                                             mode="PREVIEW" if preview else "CLOSED")
    contexts = (result.get("risk_audit") or {}).get("contexts", {})
    for row in diagnostic["checks"]:
        key = row["id"]
        tf = row.get("timeframe")
        description, paths = NATIVE.get(key, ("Compara o valor observado à exigência preservada nesta avaliação.", ()))
        detail = dict(version=VERSION, description=description, method=description,
                      parameters=parameters(cfg, paths), timeframes=[tf] if tf else [])
        if key.startswith(("data:", "age:")):
            detail.update(description="Verifica cobertura e atualidade dos candles usados na análise.",
                          method="Usa os candles disponíveis e o ATR do período; idade é medida desde o fechamento.",
                          parameters=parameters(cfg, ("stale_tf_multiplier",)))
        if key.startswith("rules:"):
            detail.update(description="Combina as condições configuradas em grupos E/OU. Abra a árvore para localizar a condição pendente.",
                          method="E exige todas as condições ativas; OU exige alguma. Dado indisponível em condição ativa mantém o grupo indisponível, inclusive em OU. Condições desativadas não participam.",
                          timeframes=[cfg.stage("trigger" if key == "rules:GATILHO" else "zone")] if key in ("rules:GATILHO", "rules:ARMADO") else [])
            detail["operational_timeframe"] = cfg.stage("trigger" if key == "rules:GATILHO" else "zone") if key in ("rules:GATILHO", "rules:ARMADO") else None
        if row.get("left") is not None:
            detail.update(description="Compara as fontes configuradas usando o operador e os valores desta avaliação.",
                          method="Os parâmetros e períodos abaixo pertencem a cada operando da condição.",
                          inputs={side: operand_detail(row[side], (row.get("operand_samples") or {}).get(side),
                                                       cfg, frames, cutoff, preview) for side in ("left", "right")})
            detail["timeframes"] = list(dict.fromkeys(v["timeframe"] for v in detail["inputs"].values() if v.get("timeframe")))
        stage = next((s for s in ("structure", "zone") if key in
                      ("audit:"+s+"_alignment", "audit:"+s+"_integrity")), None)
        if stage:
            integrity = key.endswith("_integrity")
            ctx = contexts.get(stage) or {}
            detail.update(
                description=("Verifica se a estrutura permanece íntegra após os fechamentos." if integrity else
                             "Verifica se os topos e fundos confirmados formam a direção de alta exigida."),
                method=INTEGRITY_METHOD if integrity else PIVOT_METHOD,
                parameters={**parameters(cfg, ("pivot_left", "pivot_right",
                    "context_audit.require_structure_integrity" if integrity else "context_audit.require_"+stage+"_alignment")),
                    "candle_limit": cfg.candle_limits[tf]},
                evidence=dict(direction=ctx.get("direction"), intact=ctx.get("intact"),
                              broken_at=ctx.get("broken_at"), last_break=deepcopy(ctx.get("last_break")),
                              pivots=selected_pivots(ctx.get("pivots", [])), closed_at=ctx.get("closed_at"),
                              complete=ctx.get("direction") is not None))
            detail["domain"] = "Íntegra ou rompida; sem estrutura completa, dado indisponível." if integrity else "Alta, Baixa ou Lateral; sem estrutura completa, dado indisponível."
        if key == "btc_downtrend":
            detail.update(description="Classifica a estrutura do Bitcoin pelos topos e fundos confirmados. O filtro veta a classificação de baixa.",
                          method=PIVOT_METHOD + " Neste classificador, pivôs insuficientes também resultam em Lateral; a cobertura é mostrada nos detalhes.",
                          parameters={**parameters(cfg, ("pivot_left", "pivot_right", "btc_trend_timeframe", "veto_filters.btc_downtrend")),
                                      "candle_limit": cfg.candle_limits[cfg.btc_trend_timeframe]},
                          timeframes=[cfg.btc_trend_timeframe])
            detail["domain"] = "Alta, Baixa ou Lateral. Pivôs insuficientes também são classificados como Lateral pelo método atual."
        elif tf:
            detail["window"] = window(frames.get(tf, {}), tf, cutoff, preview=preview)
        if key in ("asset_downtrend", "asset_range", "score:trend"):
            detail["method"] = PIVOT_METHOD
        row["label"] = TITLES.get(key, row["label"])
        row["explanation"] = detail
    by_id = {row["id"]: row for row in diagnostic["checks"]}
    def leaf_ids(tree):
        if "check_id" in tree:
            yield tree["check_id"]
        for child in tree.get("children", []):
            yield from leaf_ids(child)
    for row in diagnostic["checks"]:
        if row.get("rule_tree"):
            detail = row["explanation"]
            detail["timeframes"] = list(dict.fromkeys(tf for key in leaf_ids(row["rule_tree"])
                for tf in by_id[key]["explanation"]["timeframes"]))
    pattern_paths = ("min_range_atr", "doji_body_pct", "pin_wick_ratio", "pin_wick_pct", "pin_close_fraction",
                     "engulf_include_wicks", "marubozu_wick_pct")
    for pattern in diagnostic["patterns"]:
        for row in pattern["conditions"]:
            row["explanation"] = dict(version=VERSION,
                description="Verifica esta característica do padrão de candle; suas características precisam passar em conjunto.",
                method="Compara corpo, pavios, amplitude ou preços do candle com a exigência indicada.",
                parameters=parameters(cfg, pattern_paths), timeframes=[cfg.stage("trigger")],
                window=window(frames.get(cfg.stage("trigger"), {}), cfg.stage("trigger"), cutoff, preview=preview))


def complete_service_explanations(analysis, cfg, cutoff):
    """Attach already-frozen BTC evidence after the owning service supplies it."""
    diagnostic = analysis["trigger_diagnostics"]
    context = diagnostic.get("explanation_context")
    if context is not None:
        context.update(source=analysis.get("source"), config_hash=analysis.get("config_hash"),
                       observed_at=analysis.get("observed_at_utc"))
    btc = analysis.get("btc_context") or {}
    frame = (btc.get("frames") or {}).get(cfg.btc_trend_timeframe)
    for row in diagnostic["checks"]:
        if row["id"] == "evaluation_current":
            description, paths = NATIVE["evaluation_current"]
            row["explanation"] = dict(version=VERSION, description=description, method=description,
                parameters=parameters(cfg, paths), timeframes=[cfg.stage("trigger")])
        if row["id"] != "btc_downtrend" or not frame or "explanation" not in row:
            continue
        row["explanation"].update(
            window=dict(start_at=frame.get("window_start"), end_at=(frame.get("reference_candle") or {}).get("close_at"),
                        candle_count=frame.get("candle_count")),
            evidence=dict(pivots=deepcopy(frame.get("pivots_used", [])),
                          reference_candle=deepcopy(frame.get("reference_candle")), reason=frame.get("reason"),
                          complete=all(sum(p["kind"] == kind for p in frame.get("pivots_used", [])) >= 2 for kind in ("H", "L"))),
            source_symbol=btc.get("symbol"), source=btc.get("source"))
