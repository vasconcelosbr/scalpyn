-- Sync: Normalize config_profiles(block) structure for Autopilot Caminho B connectivity.
-- Parte 2.1 — one-time idempotent sync before migrating pipeline readers.
--
-- PROBLEM: config_profiles(block) was seeded with flat {"blocks": []}
-- but Autopilot writers (_adjust_block_rules, _adjust_entry_triggers) expect
-- {"block_rules": {"blocks": [...]}, "entry_triggers": {"logic": "AND", "conditions": [...]}}.
--
-- This script normalizes the structure without losing any existing data.
-- Safe to run multiple times (idempotent): only updates rows that still have the old flat form.
--
-- Run via:
--   railway connect Postgres  (depois: \i sync_block_config_structure.sql)
--   ou: psql $DATABASE_URL -f sync_block_config_structure.sql

-- Step 1: Normalize flat {"blocks": [...]} → {"block_rules": {"blocks": [...]}, "entry_triggers": {...}}
-- Only touches rows where the old flat key exists and the new nested key does NOT yet exist.
UPDATE config_profiles
SET
    config_json = jsonb_build_object(
        'block_rules', jsonb_build_object(
            'blocks', COALESCE(config_json->'blocks', '[]'::jsonb)
        ),
        'entry_triggers', jsonb_build_object(
            'logic', 'AND',
            'conditions', '[]'::jsonb
        )
    ),
    updated_at = NOW()
WHERE config_type = 'block'
  AND config_json ? 'blocks'
  AND NOT (config_json ? 'block_rules');

-- Step 2: Ensure entry_triggers key exists on rows that already have block_rules
-- (handles partial normalization from a prior partial run)
UPDATE config_profiles
SET
    config_json = config_json || jsonb_build_object(
        'entry_triggers', jsonb_build_object(
            'logic', 'AND',
            'conditions', '[]'::jsonb
        )
    ),
    updated_at = NOW()
WHERE config_type = 'block'
  AND config_json ? 'block_rules'
  AND NOT (config_json ? 'entry_triggers');

-- Verify result
SELECT
    id,
    config_json ? 'block_rules'    AS has_block_rules,
    config_json ? 'entry_triggers' AS has_entry_triggers,
    config_json ? 'blocks'         AS has_legacy_flat_blocks,
    updated_at
FROM config_profiles
WHERE config_type = 'block';
