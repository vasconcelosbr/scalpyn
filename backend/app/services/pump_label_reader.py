"""Read an unchanged FIFO prefix without costing its discarded suffix.

One statement snapshot freezes minute revisions for both costs and payloads.
The walk stops at the first over-budget identity; missing paths cost zero and
remain gaps for the unchanged labeller. No scheduling or resource cap changes.
"""

FIFO_PREFIX_SQL = """WITH RECURSIVE requests AS MATERIALIZED (
    SELECT * FROM jsonb_to_recordset(CAST(:requests AS jsonb))
        AS r(n integer,i uuid,s text,a timestamptz,b timestamptz,exact boolean)),
walk(n,accepted_count,points,bytes,seen,head_points,head_bytes,examined_points,examined_bytes) AS (
    SELECT 0,0,0::bigint,0::bigint,'{}'::jsonb,0::bigint,0::bigint,0::bigint,0::bigint
    UNION ALL
    SELECT r.n,
        CASE WHEN w.points+c.points<=:points AND w.bytes+c.bytes<=:bytes THEN r.n ELSE w.accepted_count END,
        CASE WHEN w.points+c.points<=:points AND w.bytes+c.bytes<=:bytes THEN w.points+c.points ELSE w.points END,
        CASE WHEN w.points+c.points<=:points AND w.bytes+c.bytes<=:bytes THEN w.bytes+c.bytes ELSE w.bytes END,
        w.seen||c.keys,
        CASE WHEN r.n=1 THEN c.points ELSE w.head_points END,
        CASE WHEN r.n=1 THEN c.bytes ELSE w.head_bytes END,
        w.points+c.points,w.bytes+c.bytes
    FROM walk w JOIN requests r ON r.n=w.n+1
    CROSS JOIN LATERAL (
        SELECT COALESCE(sum(jsonb_array_length(p.payload->'points')),0)::bigint AS points,
            COALESCE(sum(octet_length(p.payload::text)),0)::bigint AS bytes,
            COALESCE(jsonb_object_agg(p.path_key,true),'{}'::jsonb) AS keys
        FROM (
            SELECT DISTINCT ON(p.bucket_start) p.payload,
                p.instrument_id::text||'/'||p.bucket_start::text AS path_key
            FROM pump_opportunity_price_paths p
            WHERE r.exact AND p.user_id=:u AND p.instrument_id=r.i
                AND p.bucket_start>=r.a AND p.bucket_start<=r.b
                AND NOT(w.seen ? (p.instrument_id::text||'/'||p.bucket_start::text))
            ORDER BY p.bucket_start,p.complete DESC,p.captured_at DESC
        ) p
    ) c
    WHERE w.n=w.accepted_count),
budget AS MATERIALIZED (SELECT * FROM walk ORDER BY n DESC LIMIT 1),
admitted AS MATERIALIZED (SELECT r.* FROM requests r CROSS JOIN budget b WHERE r.n<=b.accepted_count),
bounds AS (SELECT i,min(a) AS a,max(b) AS b FROM admitted WHERE exact GROUP BY i),
selected AS (
    SELECT b.i AS instrument_id,p.payload FROM bounds b
    CROSS JOIN LATERAL (
        SELECT DISTINCT ON(p.bucket_start) p.bucket_start,p.payload
        FROM pump_opportunity_price_paths p WHERE p.user_id=:u AND p.instrument_id=b.i
            AND p.bucket_start>=b.a AND p.bucket_start<=b.b
            AND EXISTS(SELECT 1 FROM admitted r WHERE r.exact AND r.i=b.i
                AND p.bucket_start>=r.a AND p.bucket_start<=r.b)
        ORDER BY p.bucket_start,p.complete DESC,p.captured_at DESC
    ) p)
SELECT NULL::uuid AS instrument_id,NULL::jsonb AS payload,b.points,b.bytes,
    b.accepted_count=0 AS exhausted,b.accepted_count,
    b.examined_points AS requested_points,b.examined_bytes AS requested_bytes,
    b.head_points,b.head_bytes,b.n AS examined_count
FROM budget b
UNION ALL
SELECT s.instrument_id,s.payload,b.points,b.bytes,false,b.accepted_count,
    b.examined_points,b.examined_bytes,b.head_points,b.head_bytes,b.n
FROM selected s CROSS JOIN budget b"""
