"""Lint invariants — vocabulário canônico de ``decisions_log.direction``.

Task #292: ``decisions_log.direction`` usa o vocabulário canônico
``{'LONG', 'SHORT', 'NEUTRAL', 'SPOT'}`` (uppercase). Antes desta
task, o produtor (``pipeline_scan._apply_robust_authoritative_scoring``)
só populava o campo no path ``is_futures=True`` e o gate Shadow
(``shadow_trade_service``) filtrava por ``direction='up'`` (vocabulário
inexistente no resto do código). Resultado: 109 ALLOW spot/24h com
``direction=NULL``, gate Shadow nunca disparava, painel ML vazio.

Estes testes são SUBSTRING-BASED (não AST) propositalmente — o objetivo
é caçar regressão de vocabulário, não validar tipos. Cada marker tem
um comentário explicando o que está sendo testado, para facilitar
extensão (ex.: adicionar novo vocabulário válido).

Modelo: copia o shape de ``test_pipeline_symbol_ordering_invariants.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

# ── Vocabulário canônico ─────────────────────────────────────────────────────
# Adicionar novos valores aqui se o vocabulário evoluir (ex.: 'CASH', 'STAKE').
# Lowercase / colloquial ('up', 'down', 'long', 'short') é PROIBIDO.
CANONICAL_DIRECTION_VALUES = {"LONG", "SHORT", "NEUTRAL", "SPOT"}
FORBIDDEN_LOWERCASE_VALUES = {"up", "down", "sideways"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Invariante 1 ─────────────────────────────────────────────────────────────
# Produtor de decisions_log.direction (pipeline_scan.py) só escreve valores
# canônicos. Caça strings que setam asset["futures_direction"] = "<x>".

def test_pipeline_scan_only_writes_canonical_direction():
    """``pipeline_scan.py`` só pode atribuir valores canônicos a
    ``asset["futures_direction"]``. Regressão: gravar 'long' (lowercase)
    ou 'up' faria o gate Shadow falhar silenciosamente."""
    src = _read(APP_ROOT / "tasks" / "pipeline_scan.py")
    # Match: asset["futures_direction"] = "VALUE"
    pattern = re.compile(r'asset\[["\']futures_direction["\']\]\s*=\s*["\']([^"\']+)["\']')
    found = pattern.findall(src)
    assert found, (
        "Esperava encontrar ao menos uma atribuição a "
        "asset['futures_direction'] em pipeline_scan.py — talvez o "
        "setter foi removido?"
    )
    invalid = [v for v in found if v not in CANONICAL_DIRECTION_VALUES]
    assert not invalid, (
        f"pipeline_scan.py atribui valores não-canônicos a "
        f"asset['futures_direction']: {invalid}. "
        f"Allowlist: {sorted(CANONICAL_DIRECTION_VALUES)}. "
        f"Se um novo valor é necessário, adicione em "
        f"CANONICAL_DIRECTION_VALUES neste teste."
    )


# ── Invariante 2 ─────────────────────────────────────────────────────────────
# Consumidores (Shadow service, execute_buy, ML) NÃO podem filtrar por
# vocabulário lowercase ('up', 'down', 'sideways') em decisions_log.direction.

CONSUMER_FILES = [
    APP_ROOT / "services" / "shadow_trade_service.py",
    APP_ROOT / "tasks" / "execute_buy.py",
    APP_ROOT / "ml" / "dataset_builder.py",
]


@pytest.mark.parametrize("path", CONSUMER_FILES, ids=lambda p: p.name)
def test_consumers_do_not_filter_by_lowercase_direction(path: Path):
    """Nenhum consumidor de ``decisions_log.direction`` pode filtrar
    por ``'up'``/``'down'``/``'sideways'``. Se aparecer, é regressão
    do bug original que travou o Shadow Portfolio (Task #292)."""
    if not path.exists():
        pytest.skip(f"{path.name} não existe — provavelmente removido")
    src = _read(path)
    # Caça padrões: direction == 'up', direction = 'up', direction.in_(["up"]),
    # direction IN ('up'), d.direction = 'up', etc.
    forbidden_patterns = [
        re.compile(rf"direction\s*[=!]=?\s*[\"']({v})[\"']", re.IGNORECASE)
        for v in FORBIDDEN_LOWERCASE_VALUES
    ] + [
        re.compile(rf"direction\s+IN\s*\([^)]*[\"']({v})[\"']", re.IGNORECASE)
        for v in FORBIDDEN_LOWERCASE_VALUES
    ] + [
        re.compile(rf"direction\.in_\(\s*\[[^]]*[\"']({v})[\"']", re.IGNORECASE)
        for v in FORBIDDEN_LOWERCASE_VALUES
    ]
    hits = []
    for pat in forbidden_patterns:
        for m in pat.finditer(src):
            hits.append((m.group(0), m.start()))
    assert not hits, (
        f"{path.name} filtra decisions_log.direction por vocabulário "
        f"lowercase proibido: {hits}. "
        f"Use vocabulário canônico (uppercase): "
        f"{sorted(CANONICAL_DIRECTION_VALUES)}."
    )


# ── Invariante 3 ─────────────────────────────────────────────────────────────
# DatasetBuilder.encode_direction precisa ter as keys canônicas exatas.
# Se alguém remover 'SPOT' por engano, simulações Shadow viram NaN no ML.

def test_dataset_builder_direction_map_uses_canonical_keys():
    """``DatasetBuilder.direction_map`` deve mapear EXATAMENTE
    ``{'LONG', 'SHORT', 'SPOT'}`` (NEUTRAL não vira shadow, então não
    precisa de encoding ML)."""
    src = _read(APP_ROOT / "ml" / "dataset_builder.py")
    # Procura o bloco direction_map = { ... } e extrai as keys.
    block_match = re.search(
        r"direction_map\s*=\s*\{([^}]+)\}",
        src,
        re.DOTALL,
    )
    assert block_match, (
        "Não achei ``direction_map = {...}`` em dataset_builder.py — "
        "talvez foi renomeado? Atualizar este teste."
    )
    block = block_match.group(1)
    keys = set(re.findall(r'["\']([A-Z]+)["\']\s*:', block))
    expected = {"LONG", "SHORT", "SPOT"}
    assert keys == expected, (
        f"DatasetBuilder.direction_map keys = {sorted(keys)}, "
        f"esperado {sorted(expected)}. Se um novo valor canônico é "
        f"necessário, atualizar AMBOS dataset_builder.py E este teste."
    )


# ── Invariante 4 ─────────────────────────────────────────────────────────────
# Sanidade: o gate Shadow precisa filtrar EFETIVAMENTE direction usando ORM
# (DecisionLog.direction == … / .in_(…)) ou SQL raw (direction = … /
# direction IN (…)) — não basta a string canônica aparecer em comentário ou
# docstring. Validamos a presença de AMBOS (ORM + SQL raw) porque o serviço
# tem dois call sites: ``_resolve_decision`` (ORM) e ``_promote_pending_decisions``
# (SQL raw NOT EXISTS).

# Padrões aceitos como filtro EFETIVO de direction com valor canônico.
_ORM_FILTER_PATTERNS = [
    # DecisionLog.direction == "SPOT"  ou  d.direction == "SPOT"
    re.compile(
        rf'\.direction\s*==\s*["\'](?:{"|".join(CANONICAL_DIRECTION_VALUES)})["\']'
    ),
    # DecisionLog.direction.in_(["SPOT", ...])
    re.compile(
        rf'\.direction\.in_\(\s*\[[^]]*["\'](?:{"|".join(CANONICAL_DIRECTION_VALUES)})["\']'
    ),
]
_SQL_FILTER_PATTERNS = [
    # WHERE ... direction = 'SPOT'
    re.compile(
        rf'direction\s*=\s*["\'](?:{"|".join(CANONICAL_DIRECTION_VALUES)})["\']',
        re.IGNORECASE,
    ),
    # WHERE ... direction IN ('SPOT', ...)
    re.compile(
        rf'direction\s+IN\s*\([^)]*["\'](?:{"|".join(CANONICAL_DIRECTION_VALUES)})["\']',
        re.IGNORECASE,
    ),
]


def test_shadow_gate_filters_by_canonical_direction():
    """``shadow_trade_service`` precisa de filtros EFETIVOS de
    ``direction`` em ambos os call sites:

    * ORM (``_resolve_decision``): ``DecisionLog.direction == "SPOT"`` ou ``.in_([...])``
    * SQL raw (``_promote_pending_decisions``): ``direction = 'SPOT'`` ou ``IN (...)``

    Apenas substring canônica (em comentário/docstring) NÃO conta —
    o filtro pode ter sido comentado por engano."""
    src = _read(APP_ROOT / "services" / "shadow_trade_service.py")

    has_orm = any(p.search(src) for p in _ORM_FILTER_PATTERNS)
    has_sql = any(p.search(src) for p in _SQL_FILTER_PATTERNS)

    assert has_orm, (
        "shadow_trade_service.py NÃO tem filtro ORM efetivo em "
        "DecisionLog.direction usando vocabulário canônico "
        f"({sorted(CANONICAL_DIRECTION_VALUES)}). Esperado padrão "
        "como ``DecisionLog.direction == \"SPOT\"`` ou "
        "``DecisionLog.direction.in_([\"SPOT\"])``."
    )
    assert has_sql, (
        "shadow_trade_service.py NÃO tem filtro SQL raw efetivo em "
        "``direction``. Esperado padrão como ``direction = 'SPOT'`` ou "
        "``direction IN ('SPOT', ...)``."
    )
