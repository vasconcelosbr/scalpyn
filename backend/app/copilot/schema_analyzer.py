from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .query_executor import QueryExecutor


class SchemaAnalyzer:
    def __init__(self, executor: QueryExecutor | None = None):
        self.executor = executor or QueryExecutor()

    async def analyze(self, db: AsyncSession, user_id: UUID, session_id: UUID | None = None) -> dict[str, Any]:
        columns_result = await self.executor.execute(
            db, user_id,
            """
            SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
                   CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN true ELSE false END AS is_primary_key
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
              ON kcu.table_schema = c.table_schema AND kcu.table_name = c.table_name
             AND kcu.column_name = c.column_name
            LEFT JOIN information_schema.table_constraints tc
              ON tc.constraint_schema = kcu.constraint_schema AND tc.constraint_name = kcu.constraint_name
             AND tc.constraint_type = 'PRIMARY KEY'
            WHERE c.table_schema = 'public'
            ORDER BY c.table_name, c.ordinal_position
            """, {}, reason="Mapear tabelas e colunas do schema público", session_id=session_id,
        )
        fk_result = await self.executor.execute(
            db, user_id,
            """
            SELECT tc.table_name, kcu.column_name,
                   ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name AND ccu.constraint_schema = tc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
            ORDER BY tc.table_name, kcu.column_name
            """, {}, reason="Mapear foreign keys reais", session_id=session_id,
        )
        by_table: dict[str, dict[str, Any]] = {}
        for col in columns_result["rows"]:
            table = by_table.setdefault(col["table_name"], {
                "name": col["table_name"], "primary_key": None,
                "important_columns": [], "columns": [], "relationships": [],
            })
            table["columns"].append({"name": col["column_name"], "data_type": col["data_type"],
                                     "nullable": col["is_nullable"] == "YES"})
            if col["is_primary_key"]:
                table["primary_key"] = col["column_name"]
            if col["column_name"] in {"id", "user_id", "profile_id", "run_id", "trade_id", "model_id", "symbol", "source", "created_at", "updated_at"}:
                table["important_columns"].append(col["column_name"])
        real_edges = set()
        for fk in fk_result["rows"]:
            edge = (fk["table_name"], fk["column_name"], fk["foreign_table_name"], fk["foreign_column_name"])
            real_edges.add(edge)
            if fk["table_name"] in by_table:
                by_table[fk["table_name"]]["relationships"].append({
                    "target_table": fk["foreign_table_name"], "source_column": fk["column_name"],
                    "target_column": fk["foreign_column_name"], "type": "foreign_key",
                })
        inferable = {"profile_id": "profiles", "run_id": "profile_intelligence_runs",
                     "model_id": "ml_models", "trade_id": "trades"}
        for table in by_table.values():
            names = {col["name"] for col in table["columns"]}
            for source_column, target_table in inferable.items():
                edge = (table["name"], source_column, target_table, "id")
                if source_column in names and target_table in by_table and edge not in real_edges and table["name"] != target_table:
                    table["relationships"].append({
                        "target_table": target_table, "source_column": source_column,
                        "target_column": "id", "type": "inferred_by_name",
                    })
        return {"tables": list(by_table.values()), "queries": [columns_result, fk_result],
                "truncated": columns_result["truncated"] or fk_result["truncated"]}
