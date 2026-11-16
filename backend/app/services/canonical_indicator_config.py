"""Explicit identity for the shared (non-tenant-keyed) indicator producer."""
import os
from uuid import UUID

from sqlalchemy import text

from .profile_runtime_config import canonical_hash


async def load_canonical_indicator_config(db):
    # CANONICAL_INDICATOR_CONFIG_SELECTOR: never pick another tenant by recency.
    # This is an infrastructure identity, not a copy of the GUI-editable values.
    selected = os.getenv('CANONICAL_INDICATOR_CONFIG_PROFILE_ID', '').strip()
    params = {}
    selector = ''
    if selected:
        params['config_id'] = UUID(selected)  # malformed selectors fail closed
        selector = ' AND id = :config_id'
    rows = (await db.execute(text(
        "SELECT id, user_id, config_json, updated_at FROM config_profiles "
        "WHERE config_type = 'indicators' AND is_active IS TRUE" + selector
    ), params)).mappings().all()
    if len(rows) != 1:
        raise RuntimeError(
            f'INDICATOR_CONFIG_CARDINALITY_INVALID: expected=1 actual={len(rows)}'
        )
    row = rows[0]
    config = dict(row['config_json'] or {})
    if not config:
        raise RuntimeError('INDICATOR_CONFIG_EMPTY')
    return config, {
        'config_profile_id': str(row['id']),
        'config_user_id': str(row['user_id']),
        'config_updated_at': row['updated_at'].isoformat(),
        'config_hash': canonical_hash(config),
    }
