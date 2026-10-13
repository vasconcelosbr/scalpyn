"""Read-only inventory; credentials provided in PUMP_AUDIT_VARS, never persisted."""
import json
import os
from pathlib import Path
import psycopg

queries = {
    'cutoff': 'SELECT now() cutoff,pg_database_size(current_database()) database_bytes',
    'largest': "SELECT n.nspname schema,c.relname table_name,pg_total_relation_size(c.oid) total_bytes,pg_table_size(c.oid) table_bytes,pg_indexes_size(c.oid) index_bytes,s.n_live_tup estimated_live_rows,s.n_dead_tup estimated_dead_rows,s.last_autovacuum,s.last_autoanalyze FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid WHERE c.relkind='r' AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' ORDER BY total_bytes DESC LIMIT 30",
    'extensions': 'SELECT extname FROM pg_extension',
    'partition_parents': "SELECT inhparent::regclass::text parent,count(*) partitions,sum(pg_total_relation_size(inhrelid)) total_bytes FROM pg_inherits GROUP BY inhparent ORDER BY total_bytes DESC",
    'timestamps': "SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND data_type LIKE 'timestamp%' ORDER BY table_name,ordinal_position",
    'foreign_keys': "SELECT conrelid::regclass::text table_name,confrelid::regclass::text references_table,pg_get_constraintdef(oid) definition FROM pg_constraint WHERE contype='f' ORDER BY 1",
}
out = {}
url = json.loads(os.environ['PUMP_AUDIT_VARS'])['DATABASE_PUBLIC_URL']
with psycopg.connect(url, options='-c default_transaction_read_only=on -c statement_timeout=20000') as conn:
    for name, query in queries.items():
        try:
            with conn.transaction():
                cur = conn.execute(query)
                rows = [dict(zip([col.name for col in cur.description], row)) for row in cur.fetchall()]
                out[name] = {'query': query, 'rows': rows}
                if name not in ('timestamps', 'foreign_keys'):
                    print(json.dumps({name: out[name]}, default=str))
        except Exception as exc:
            out[name] = {'error': type(exc).__name__}
Path(__file__).with_name('database-inventory.json').write_text(json.dumps(out, default=str, indent=2), encoding='utf-8')
