"""Task #316 invariant — exit-metrics capture nunca enumera chaves.

Runbook §4.1: o catálogo é dinâmico (indicators_provider). Qualquer
lista/tupla/set literal de nomes de indicadores nos call-sites de
exit-metrics é regressão (engenheiro futuro fixou rsi/macd/adx em um
patch rápido e quebrou a paridade).

Estes lints são intencionalmente baratos (substring check) — o objetivo
é pegar o anti-padrão óbvio (``["rsi", "macd", ...]``) sem AST overhead.
"""

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "app"

# Indicadores comuns que NÃO podem aparecer enumerados nos call-sites.
# Lista deliberadamente curta — o lint testa que NENHUM aparece junto
# com outro num literal (proxy para "lista de chaves").
_FORBIDDEN_KEYS = ("rsi", "macd", "adx", "ema9", "ema21", "bb_upper")

# Arquivos onde o anti-padrão seria especialmente prejudicial.
_GUARDED_FILES = [
    BACKEND / "services" / "exit_metrics.py",
    BACKEND / "services" / "trade_monitor_service.py",
    BACKEND / "tasks" / "shadow_trade_monitor.py",
]


def _contains_indicator_literal_list(source: str) -> bool:
    """True se mais de 1 das chaves proibidas aparecem JUNTAS no mesmo
    arquivo (não-comment).

    Não tenta parser real — heurística: conta quantas chaves aparecem
    em literais string (entre aspas). Se ≥3, é quase certeza que é uma
    lista enumerada.
    """
    # Remove comentários simples linha-a-linha (não pega docstrings —
    # docstrings que mencionam indicadores são intencionais e ok).
    lines = []
    for line in source.splitlines():
        stripped = line.split("#", 1)[0]
        lines.append(stripped)
    cleaned = "\n".join(lines)

    hits = 0
    for key in _FORBIDDEN_KEYS:
        # Conta apenas occorrências entre aspas (string literal).
        if f'"{key}"' in cleaned or f"'{key}'" in cleaned:
            hits += 1
    return hits >= 3


def test_no_hardcoded_indicator_list_in_exit_metrics_callsites():
    offenders = []
    for path in _GUARDED_FILES:
        assert path.exists(), f"guarded file missing: {path}"
        source = path.read_text(encoding="utf-8")
        if _contains_indicator_literal_list(source):
            offenders.append(str(path))
    assert not offenders, (
        "exit-metrics call-sites contém lista literal de chaves de "
        "indicadores (regressão do anti-padrão runbook §4.1). "
        f"Arquivos: {offenders}"
    )


def test_exit_metrics_internal_keys_constant_is_final_frozenset():
    """Lint textual: a constante deve ser declarada como Final[frozenset]."""
    src = (BACKEND / "services" / "exit_metrics.py").read_text(encoding="utf-8")
    assert "EXIT_METRICS_INTERNAL_KEYS: Final[frozenset[str]]" in src, (
        "EXIT_METRICS_INTERNAL_KEYS deve ser declarada como "
        "``Final[frozenset[str]]`` para impedir mutação acidental."
    )


def test_capture_helper_imported_in_both_monitors():
    """build_exit_snapshot deve ser usado em ambos os pontos de saída
    (TradeMonitorService + shadow_trade_monitor) — runbook Fase B."""
    tm = (BACKEND / "services" / "trade_monitor_service.py").read_text(
        encoding="utf-8"
    )
    sm = (BACKEND / "tasks" / "shadow_trade_monitor.py").read_text(
        encoding="utf-8"
    )
    assert "build_exit_snapshot" in tm, (
        "TradeMonitorService precisa importar/chamar build_exit_snapshot"
    )
    assert "build_exit_snapshot" in sm, (
        "shadow_trade_monitor precisa rotear pelo helper canônico"
    )
