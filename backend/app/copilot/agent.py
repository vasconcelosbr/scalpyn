import json
import logging
import os
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile import Profile
from ..services.ai_keys_service import get_decrypted_api_key
from .action_service import action_service
from .prompt import BASE_PROMPT
from .query_executor import QueryExecutor
from .schema_analyzer import SchemaAnalyzer
from .skill_service import skill_service


logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 6
FINAL_SYNTHESIS_INSTRUCTION = (
    "\n\nO limite de uso de ferramentas foi atingido. Não use mais ferramentas. "
    "Responda agora com as evidências já coletadas, declare limitações e indique o próximo passo."
)


TOOLS = [
    {"name": "run_readonly_query", "description": "Executa uma única query SQL read-only, limitada e auditada.",
     "input_schema": {"type": "object", "properties": {
         "sql": {"type": "string"}, "params": {"type": "object"}, "reason": {"type": "string"}},
         "required": ["sql", "reason"]}},
    {"name": "get_schema_map", "description": "Retorna tabelas, colunas e relacionamentos reais/inferidos.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_profile_config", "description": "Busca a configuração atual de um profile do usuário.",
     "input_schema": {"type": "object", "properties": {"profile_id": {"type": "string"}}, "required": ["profile_id"]}},
    {"name": "create_action_plan", "description": "Cria DRY_RUN para ajustar config de profile; nunca executa escrita no profile.",
     "input_schema": {"type": "object", "properties": {
         "profile_id": {"type": "string"}, "objective": {"type": "string"},
         "evidence": {"type": "object"}, "risk": {"type": "string"},
         "changes": {"type": "array", "items": {"type": "object", "properties": {
             "path": {"type": "string"}, "old_value": {}, "new_value": {}, "reason": {"type": "string"}},
             "required": ["path", "new_value", "reason"]}}},
         "required": ["profile_id", "objective", "changes", "risk"]}},
    {"name": "retrieve_skills", "description": "Recupera conhecimento operacional ativo e versionado.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "save_skill_candidate", "description": "Salva aprendizado; tipos críticos ficam pendentes de aprovação.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "skill_type": {"type": "string"},
         "content": {"type": "string"}, "confidence": {"type": "number"}, "source": {"type": "string"}},
         "required": ["name", "skill_type", "content"]}},
]


class CopilotAgent:
    def __init__(self):
        self.query_executor = QueryExecutor()
        self.schema_analyzer = SchemaAnalyzer(self.query_executor)

    async def run(self, db: AsyncSession, user_id: UUID, message: str, *, session_id: UUID,
                  context: dict[str, Any], provider: str, model: str | None = None):
        skills = await skill_service.retrieve(db, user_id, message)
        system = BASE_PROMPT + "\nContexto da tela:\n" + json.dumps(context, ensure_ascii=False)
        if skills:
            system += "\n\nSkills recuperadas:\n" + "\n".join(
                f"- [{item['skill_type']} v{item['version']}] {item['name']}: {item['content']}" for item in skills
            )
        trace = {"queries": [], "evidence": [], "action_plan": None, "skills_used": skills}
        if provider == "openai":
            answer = await self._run_openai(db, user_id, message, session_id, system, model, trace)
        else:
            answer = await self._run_anthropic(db, user_id, message, session_id, system, model, trace)
        return {"answer": answer, **trace}

    async def _tool(self, db: AsyncSession, user_id: UUID, session_id: UUID,
                    name: str, payload: dict[str, Any], trace: dict[str, Any]):
        if name == "run_readonly_query":
            result = await self.query_executor.execute(
                db, user_id, payload["sql"], payload.get("params") or {},
                reason=payload["reason"], session_id=session_id,
            )
            trace["queries"].append(result)
            trace["evidence"].append({"tool": name, "rows": result["rows"], "query_hash": result["query_hash"]})
            return result
        if name == "get_schema_map":
            result = await self.schema_analyzer.analyze(db, user_id, session_id)
            trace["queries"].extend(result.pop("queries"))
            trace["evidence"].append({"tool": name, "table_count": len(result["tables"])})
            return result
        if name == "get_profile_config":
            profile = (await db.execute(select(Profile).where(
                Profile.id == UUID(payload["profile_id"]), Profile.user_id == user_id
            ))).scalar_one_or_none()
            if not profile:
                raise LookupError("Profile não encontrado")
            result = {"id": str(profile.id), "name": profile.name, "config": profile.config,
                      "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
                      "is_shadow_only": profile.is_shadow_only, "live_trading_enabled": profile.live_trading_enabled}
            trace["evidence"].append({"tool": name, "profile_id": str(profile.id)})
            return result
        if name == "create_action_plan":
            result = await action_service.create_dry_run(
                db, user_id, profile_id=UUID(payload["profile_id"]), objective=payload["objective"],
                evidence=payload.get("evidence") or {}, changes=payload["changes"], risk=payload["risk"],
                session_id=session_id,
            )
            trace["action_plan"] = result
            return result
        if name == "retrieve_skills":
            return await skill_service.retrieve(db, user_id, payload["query"])
        if name == "save_skill_candidate":
            return await skill_service.create(
                db, user_id, name=payload["name"], skill_type=payload["skill_type"],
                content=payload["content"], metadata={"session_id": str(session_id)},
                confidence=payload.get("confidence"), source=payload.get("source") or "copilot_chat",
                actor_user_id=user_id,
            )
        raise ValueError(f"Tool desconhecida: {name}")

    async def _run_anthropic(self, db, user_id, message, session_id, system, model, trace):
        api_key = await get_decrypted_api_key(db, user_id, "anthropic") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Configure uma chave Anthropic em Settings → AI Integrations")
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        selected_model = model or os.getenv("COPILOT_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.messages.create(
                model=selected_model, max_tokens=3000, system=system, tools=TOOLS, messages=messages,
            )
            blocks = [block.model_dump() if hasattr(block, "model_dump") else block for block in response.content]
            messages.append({"role": "assistant", "content": blocks})
            tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            if not tool_uses:
                return "\n".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text").strip()
            results = []
            for use in tool_uses:
                try:
                    value = await self._tool(db, user_id, session_id, use.name, dict(use.input), trace)
                    content = json.dumps(value, ensure_ascii=False, default=str)
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": content})
                except Exception as exc:
                    results.append({"type": "tool_result", "tool_use_id": use.id,
                                    "content": f"{type(exc).__name__}: {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
        logger.warning("Copilot Anthropic atingiu o limite de ferramentas; forçando síntese final")
        response = await client.messages.create(
            model=selected_model, max_tokens=3000,
            system=system + FINAL_SYNTHESIS_INSTRUCTION, messages=messages,
        )
        return "\n".join(
            getattr(block, "text", "") for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip() or "Não foi possível concluir com as evidências disponíveis."

    async def _run_openai(self, db, user_id, message, session_id, system, model, trace):
        api_key = await get_decrypted_api_key(db, user_id, "openai") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Configure uma chave OpenAI em Settings → AI Integrations")
        import httpx
        selected_model = model or os.getenv("COPILOT_OPENAI_MODEL", "gpt-4.1-mini")
        tools = [{"type": "function", "function": {
            "name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]
        }} for tool in TOOLS]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": message}]
        async with httpx.AsyncClient(timeout=90) as client:
            for _ in range(MAX_TOOL_ROUNDS):
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": selected_model, "messages": messages, "tools": tools, "tool_choice": "auto"},
                )
                response.raise_for_status()
                assistant = response.json()["choices"][0]["message"]
                messages.append(assistant)
                calls = assistant.get("tool_calls") or []
                if not calls:
                    return assistant.get("content") or ""
                for call in calls:
                    try:
                        payload = json.loads(call["function"]["arguments"] or "{}")
                        value = await self._tool(db, user_id, session_id, call["function"]["name"], payload, trace)
                        content = json.dumps(value, ensure_ascii=False, default=str)
                    except Exception as exc:
                        content = f"{type(exc).__name__}: {exc}"
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            logger.warning("Copilot OpenAI atingiu o limite de ferramentas; forçando síntese final")
            messages[0]["content"] += FINAL_SYNTHESIS_INSTRUCTION
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": selected_model, "messages": messages, "tools": tools, "tool_choice": "none"},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"].get("content") or (
                "Não foi possível concluir com as evidências disponíveis."
            )


copilot_agent = CopilotAgent()
