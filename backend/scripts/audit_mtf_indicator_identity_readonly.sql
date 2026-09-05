\pset pager off

SELECT jsonb_pretty(jsonb_build_object(
  'config_type', config_type,
  'config_json', config_json
))
  FROM config_profiles
 WHERE is_active IS TRUE
   AND config_type ILIKE '%indicator%'
 ORDER BY updated_at DESC
 LIMIT 1;

WITH latest AS (
  SELECT DISTINCT ON (timeframe)
         timeframe, time, indicators_json
    FROM indicators
   WHERE market_type = 'spot'
     AND scheduler_group = 'structural'
     AND timeframe IN ('1h', '15m')
   ORDER BY timeframe, time DESC
)
SELECT jsonb_pretty(jsonb_build_object(
  'timeframe', timeframe,
  'computed_at', time,
  'adx', indicators_json->'adx',
  'volume_spike', indicators_json->'volume_spike',
  'bb_width', indicators_json->'bb_width'
))
  FROM latest
 ORDER BY timeframe;
