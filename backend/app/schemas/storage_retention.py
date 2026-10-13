"""Operational retention only; trading data is not on the allowlist."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StorageRetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    mode: Literal["dry_run", "execute"] = "dry_run"
    cadence_seconds: int = Field(3600, ge=60)
    batch_rows: int = Field(1000, ge=1, le=10000)
    max_batches_per_run: int = Field(10, ge=3, le=100)
    statement_timeout_ms: int = Field(3000, ge=100, le=30000)
    max_temp_file_mb: int = Field(16, ge=1, le=1024)
    lock_timeout_ms: int = Field(500, ge=1, le=3000)
    max_runtime_seconds: int = Field(60, ge=1, le=120)
    telemetry_retention_days: int = Field(7, ge=1)
    completed_label_queue_retention_days: int = Field(7, ge=1)
    # A fresh sample certifies that the most recent evidence for that key is retained.
    fresh_sample_rows: int = Field(10000, ge=1, le=50000)
    max_wal_bytes_per_run: int = Field(100000000, ge=1000000)
    preserve_model_and_shadow_evidence: Literal[True] = True
    archive_required_for_research_datasets: Literal[True] = True
    archive_before_delete: Literal[True] = True
