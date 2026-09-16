"""Immutable L3 definitions in the existing ML contract tables."""
import json
from sqlalchemy import text
from .l3_integrity import contract_definitions
from .feature_extractor import feature_columns_hash


async def register_l3_contracts(db, config, feature_names):
    spec = contract_definitions(config, feature_names)
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    # IDs are hex strings in these legacy registries and UUID columns in models.
    # Resolve UUID references with replace(id::text, '-', '') when joining.
    await db.execute(text("""
        INSERT INTO ml_label_contracts (id,name,version,description,sql_expression,target_window_seconds)
        VALUES (:id,'L3_PROFILE',:id,:description,:formula,:window)
        ON CONFLICT (id) DO NOTHING
    """), {"id": spec["label_id"], "description": encode(spec["label"]),
           "formula": spec["label"]["formula"],
           "window": config["ml_win_fast_threshold_seconds"] if config["ml_label_objective"] == "fast_tp" else None})
    await db.execute(text("""
        INSERT INTO ml_feature_contracts (id,schema_version,feature_columns_hash,feature_count,feature_columns_json,description)
        VALUES (:id,:id,:hash,:count,CAST(:columns AS jsonb),:description)
        ON CONFLICT (id) DO NOTHING
    """), {"id": spec["feature_id"], "hash": feature_columns_hash(feature_names), "count": len(feature_names),
           "columns": encode(feature_names), "description": encode(spec["features"])})
    await db.execute(text("""
        INSERT INTO ml_dataset_contracts (id,label_contract_id,feature_contract_id,source_filter,model_lane,description)
        VALUES (:id,:label,:feature,'L3','L3_PROFILE',:description)
        ON CONFLICT (id) DO NOTHING
    """), {"id": spec["dataset_id"], "label": spec["label_id"], "feature": spec["feature_id"], "description": encode(spec["dataset"])})
    for table, kind, key in (("ml_label_contracts", "label", "label_id"),
                             ("ml_feature_contracts", "features", "feature_id"),
                             ("ml_dataset_contracts", "dataset", "dataset_id")):
        stored = (await db.execute(text(f"SELECT description FROM {table} WHERE id=:id"), {"id": spec[key]})).scalar_one()
        if json.loads(stored) != spec[kind]:
            raise ValueError("l3_immutable_contract_conflict:" + kind)
    return spec
