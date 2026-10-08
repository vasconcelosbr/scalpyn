"""Unfreeze candle-training defaults pinned by the full-config writes (v1.17).

Until v1.17 every pump_opportunity save stored the whole merged config, so the
6-hourly listing refresh froze ``research.candle`` at the defaults of that moment
and the 2026-10-08 defaults (100k rows, 12 per time, horizons [15, 10],
compare ["path_mean"]) never applied. This removes ONLY keys still holding the
exact pre-change defaults; any other value (a real choice) is left untouched.
Each changed row gets a ``config_audit_log`` entry. Downgrade is a no-op (the
audit log keeps the previous JSON).

Revision ID: 240_pump_unfreeze_candle
Revises: 239_pump_flow_history
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "240_pump_unfreeze_candle"
down_revision = "239_pump_flow_history"
branch_labels = None
depends_on = None

OLD_DEFAULTS = {"max_rows": 40000, "max_rows_per_time": 5, "horizons_minutes": [10, 15],
                "compare_label_modes": ["endpoint", "path_mean"]}


def unfreeze(cfg):
    """Return (new_cfg, removed_keys); pure, for tests."""
    candle = ((cfg or {}).get("research") or {}).get("candle")
    if not isinstance(candle, dict):
        return cfg, []
    removed = [k for k, v in OLD_DEFAULTS.items() if k in candle and candle[k] == v]
    if not removed:
        return cfg, []
    new = json.loads(json.dumps(cfg))
    for k in removed:
        del new["research"]["candle"][k]
    return new, removed


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, user_id, config_json FROM config_profiles WHERE config_type = 'pump_opportunity'")).all()
    for pid, uid, cfg in rows:
        cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
        new, removed = unfreeze(cfg)
        if not removed:
            continue
        conn.execute(sa.text("UPDATE config_profiles SET config_json = CAST(:j AS jsonb), updated_at = now() "
                             "WHERE id = :id"), {"j": json.dumps(new), "id": pid})
        conn.execute(sa.text(
            "INSERT INTO config_audit_log (id, config_id, changed_by, previous_json, new_json, change_description, "
            "changed_at) VALUES (gen_random_uuid(), :id, :u, CAST(:p AS jsonb), CAST(:n AS jsonb), :d, now())"),
            {"id": pid, "u": uid, "p": json.dumps(cfg), "n": json.dumps(new),
             "d": "migration 240: unfreeze research.candle defaults " + ",".join(removed)})


def downgrade():
    pass
