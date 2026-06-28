"""Shared metric contract definitions.

A metric contract describes the provenance of a metric: source table,
shadow portfolio view, temporal window, filters, and aggregation method.
Used by Overview, Calibration Evolution and Shadow Portfolio endpoints.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Maps source codes to their Shadow Portfolio tab representation.
SHADOW_SOURCE_CONTRACT: Dict[str, Dict[str, str]] = {
    "L3": {
        "view": "Aprovados (L3)",
        "tab": "Aprovados",
        "description": "Decisões ALLOW do L3 — trades que passaram pelo filtro",
        "purpose": "Medir o que o L3 deixaria operar em produção",
        "sql_filter": "source = 'L3'",
    },
    "L3_REJECTED": {
        "view": "Rejeitados (L3)",
        "tab": "Rejeitados",
        "description": "Decisões BLOCK/REJECT do L3 — trades bloqueados pelo filtro",
        "purpose": "Medir oportunidades descartadas pelo filtro L3",
        "sql_filter": "source = 'L3_REJECTED'",
    },
    "L3_SIMULATED": {
        "view": "Simulados (L3)",
        "tab": "Simulados",
        "description": "Universo contrafactual — candidatos do L3 sem filtro ALLOW/BLOCK",
        "purpose": "Comparar performance com e sem filtro L3",
        "sql_filter": "source = 'L3_SIMULATED'",
    },
    "L1_SPECTRUM": {
        "view": "Dataset ML (L1)",
        "tab": "Dataset ML",
        "description": "Captura bruta do scanner L1, antes das regras L3",
        "purpose": "Dataset de treino/validação do modelo L1",
        "sql_filter": "source = 'L1_SPECTRUM'",
    },
    "L3_LAB": {
        "view": "Strategy Lab",
        "tab": "Strategy Lab",
        "description": "Watchlists experimentais e combinações do laboratório de estratégias",
        "purpose": "Testar hipóteses e calibrações antes de promover ao L3",
        "sql_filter": "source = 'L3_LAB'",
    },
}


def build_metric_contract(
    metric_id: str,
    label: str,
    source_table: str,
    aggregation_type: str,
    aggregation_level: str,
    formula: str,
    window_label: Optional[str] = None,
    window_hours: Optional[int] = None,
    window_field: Optional[str] = None,
    shadow_sources: Optional[list] = None,
    shadow_portfolio_views: Optional[list] = None,
    filters: Optional[Dict[str, Any]] = None,
    unit: str = "percent",
    comparable_with: Optional[list] = None,
    not_comparable_with: Optional[list] = None,
    is_snapshot: bool = False,
    snapshot_computed_at: Optional[str] = None,
    warning: Optional[str] = None,
) -> Dict[str, Any]:
    contract: Dict[str, Any] = {
        "metric_id": metric_id,
        "label": label,
        "source_table": source_table,
        "shadow_sources": shadow_sources or [],
        "shadow_portfolio_views": shadow_portfolio_views or [],
        "aggregation": {
            "type": aggregation_type,
            "level": aggregation_level,
            "formula": formula,
        },
        "filters": filters or {},
        "unit": unit,
        "comparable_with": comparable_with or [],
        "not_comparable_with": not_comparable_with or [],
        "is_snapshot": is_snapshot,
    }
    if window_label:
        contract["window"] = {
            "label": window_label,
            "window_hours": window_hours,
            "field": window_field,
        }
    if is_snapshot and snapshot_computed_at:
        contract["snapshot_computed_at"] = snapshot_computed_at
    if warning:
        contract["warning"] = warning
    return contract
