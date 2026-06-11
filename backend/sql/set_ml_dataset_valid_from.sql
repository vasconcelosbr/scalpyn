-- B4: Set ml_dataset_valid_from in the ML config profile.
-- Run ONCE after deploying the B1 fix (features now captured live).
-- This timestamp marks the boundary: shadows created before it have
-- empty features_snapshot and must NOT be included in ML training.
--
-- Rule: ml_dataset_valid_from only moves FORWARD. Never set it to a
-- past value (that would re-introduce pre-fix empty-feature records).
--
-- Verify before running:
--   SELECT config_json->>'ml_dataset_valid_from' FROM config_profiles WHERE config_type='ml';

UPDATE config_profiles
SET config_json = config_json || jsonb_build_object(
    'ml_dataset_valid_from', to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
)
WHERE config_type = 'ml'
  AND is_active = true;

-- Confirm:
SELECT config_json->>'ml_dataset_valid_from' AS valid_from
FROM config_profiles
WHERE config_type = 'ml' AND is_active = true;
