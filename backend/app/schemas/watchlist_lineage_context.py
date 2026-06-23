"""WatchlistLineageContext — snapshot da watchlist que originou um shadow trade.

Passado pelos callers no pipeline_scan para os helpers de criação em
shadow_trade_service. Gravado diretamente nas colunas de lineage de
shadow_trades (migration 103).

lineage_confidence valores válidos:
  EXACT              — watchlist_id resolvido diretamente do pipeline_scan (inline)
  JOIN_PROFILE_UNIQUE — backfill via profile_id JOIN (1 watchlist por profile)
  AMBIGUOUS_PROFILE  — backfill: mais de 1 watchlist com o mesmo profile_id
  UNRESOLVED         — backfill tentado mas sem dados suficientes
  LEGACY_UNKNOWN     — shadow histórico anterior a migration 103; sem backfill possível
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class WatchlistLineageContext:
    watchlist_id: Optional[str] = None
    watchlist_name: Optional[str] = None
    watchlist_level: Optional[str] = None
    source_watchlist_id: Optional[str] = None
    profile_id: Optional[str] = None
    profile_name: Optional[str] = None
    lineage_confidence: str = "EXACT"
    lineage_source: str = "pipeline_scan"
    lineage_resolved_at: Optional[datetime] = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
