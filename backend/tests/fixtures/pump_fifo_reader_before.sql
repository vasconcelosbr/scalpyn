WITH requests AS (
            SELECT * FROM jsonb_to_recordset(CAST(:requests AS jsonb)) AS r(n integer,i uuid,s text,a timestamptz,b timestamptz,exact boolean)),
            selected AS MATERIALIZED (SELECT DISTINCT ON(p.instrument_id,p.bucket_start) p.instrument_id,p.payload,r.n AS first_request
                FROM pump_opportunity_price_paths p JOIN requests r ON r.exact AND p.instrument_id=r.i
                    AND p.bucket_start>=r.a AND p.bucket_start<=r.b
                WHERE p.user_id=:u ORDER BY p.instrument_id,p.bucket_start,p.complete DESC,p.captured_at DESC,r.n),
            costs AS (SELECT first_request,sum(jsonb_array_length(payload->'points')) AS points,
                sum(octet_length(payload::text)) AS bytes FROM selected GROUP BY first_request),
            cumulative AS (SELECT first_request,sum(points) OVER(ORDER BY first_request) AS points,
                sum(bytes) OVER(ORDER BY first_request) AS bytes FROM costs),
            prefix AS (SELECT COALESCE(min(first_request) FILTER(WHERE points>:points OR bytes>:bytes),:count+1)-1 AS accepted_count FROM cumulative),
            budget AS (SELECT p.accepted_count,
                COALESCE(sum(jsonb_array_length(s.payload->'points')) FILTER(WHERE s.first_request<=p.accepted_count),0) AS points,
                COALESCE(sum(octet_length(s.payload::text)) FILTER(WHERE s.first_request<=p.accepted_count),0) AS bytes,
                COALESCE(sum(jsonb_array_length(s.payload->'points')) FILTER(WHERE s.first_request=1),0) AS head_points,
                COALESCE(sum(octet_length(s.payload::text)) FILTER(WHERE s.first_request=1),0) AS head_bytes,
                COALESCE(sum(jsonb_array_length(s.payload->'points')),0) AS requested_points,
                COALESCE(sum(octet_length(s.payload::text)),0) AS requested_bytes
                FROM prefix p LEFT JOIN selected s ON true GROUP BY p.accepted_count)
            SELECT NULL::uuid AS instrument_id,NULL::jsonb AS payload,b.points,b.bytes,b.accepted_count=0 AS exhausted,
                b.accepted_count,b.requested_points,b.requested_bytes,b.head_points,b.head_bytes FROM budget b
            UNION ALL SELECT s.instrument_id,s.payload,b.points,b.bytes,false,b.accepted_count,b.requested_points,b.requested_bytes,b.head_points,b.head_bytes
                FROM selected s CROSS JOIN budget b WHERE s.first_request<=b.accepted_count