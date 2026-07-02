"""Fail-closed SQL classifier for Co-Pilot read-only queries."""

from dataclasses import dataclass
import hashlib
import re


class SqlGuardError(ValueError):
    pass


@dataclass(frozen=True)
class GuardResult:
    classification: str
    normalized_sql: str
    query_hash: str


_BLOCKED_WORDS = {
    "alter", "analyze", "call", "comment", "copy", "create", "delete",
    "do", "drop", "execute", "grant", "insert", "lock", "merge", "notify",
    "prepare", "reassign", "refresh", "reindex", "reset", "revoke", "set",
    "truncate", "update", "vacuum",
}
_DANGEROUS_FUNCTIONS = {
    "dblink", "lo_export", "lo_import", "pg_read_binary_file", "pg_read_file",
    "pg_sleep", "pg_terminate_backend", "pg_write_file", "set_config",
}
_SENSITIVE_RELATIONS = {
    "ai_provider_keys", "exchange_connections", "pg_authid", "pg_shadow",
}


def _mask_literals_and_comments(sql: str) -> str:
    out: list[str] = []
    i = 0
    state = "normal"
    dollar_tag = ""
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if state == "normal":
            if ch == "'":
                state = "single"
                out.append(" ")
            elif ch == '"':
                state = "double"
                out.append(ch)
            elif ch == "-" and nxt == "-":
                state = "line_comment"
                out.extend("  ")
                i += 1
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                out.extend("  ")
                i += 1
            elif ch == "$":
                match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar"
                    out.extend(" " * len(dollar_tag))
                    i += len(dollar_tag) - 1
                else:
                    out.append(ch)
            else:
                out.append(ch)
        elif state == "single":
            out.append(" ")
            if ch == "'" and nxt == "'":
                out.append(" ")
                i += 1
            elif ch == "'":
                state = "normal"
        elif state == "double":
            out.append(ch)
            if ch == '"' and nxt == '"':
                out.append(nxt)
                i += 1
            elif ch == '"':
                state = "normal"
        elif state == "line_comment":
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                state = "normal"
        elif state == "block_comment":
            out.append(" ")
            if ch == "*" and nxt == "/":
                out.append(" ")
                i += 1
                state = "normal"
        elif state == "dollar":
            if sql.startswith(dollar_tag, i):
                out.extend(" " * len(dollar_tag))
                i += len(dollar_tag) - 1
                state = "normal"
            else:
                out.append(" ")
        i += 1
    if state in {"single", "double", "block_comment", "dollar"}:
        raise SqlGuardError("SQL contém literal, identificador ou comentário não finalizado")
    return "".join(out)


def classify_sql(sql: str) -> GuardResult:
    stripped = sql.strip()
    if not stripped:
        raise SqlGuardError("SQL vazio")
    masked = _mask_literals_and_comments(stripped)
    statements = [part for part in masked.split(";") if part.strip()]
    if len(statements) != 1:
        raise SqlGuardError("Somente uma statement é permitida")
    normalized = re.sub(r"\s+", " ", stripped.rstrip(";").strip())
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", masked.lower())
    if not words:
        raise SqlGuardError("SQL inválido")
    first = words[0]
    if first not in {"select", "with", "explain"}:
        classification = "DDL_BLOCKED" if first in {"alter", "create", "drop", "truncate"} else "WRITE_BLOCKED"
        raise SqlGuardError(f"{classification}: somente SELECT, WITH ... SELECT e EXPLAIN SELECT são permitidos")
    blocked = sorted(set(words) & _BLOCKED_WORDS)
    if blocked:
        raise SqlGuardError(f"WRITE_BLOCKED: token proibido: {blocked[0]}")
    dangerous = sorted(set(words) & _DANGEROUS_FUNCTIONS)
    if dangerous:
        raise SqlGuardError(f"DANGEROUS_BLOCKED: função proibida: {dangerous[0]}")
    sensitive = sorted(set(words) & _SENSITIVE_RELATIONS)
    if sensitive:
        raise SqlGuardError(f"DANGEROUS_BLOCKED: relação sensível: {sensitive[0]}")
    if re.search(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", masked, re.I):
        raise SqlGuardError("WRITE_BLOCKED: locking SELECT não é permitido")
    if first == "explain" and not re.search(r"^\s*explain\s+(\([^)]*\)\s*)?(select|with)\b", masked, re.I):
        raise SqlGuardError("EXPLAIN só pode ser usado com SELECT")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return GuardResult("READ_ONLY", normalized, digest)
