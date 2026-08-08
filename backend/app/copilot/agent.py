import json
import os
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile import Profile
from ..models.ai_provider_key import AIProviderKey
from ..services.ai_keys_service import get_decrypted_api_key
from ..ai_orchestration.provider_adapters import CopilotProviderTransport
from ..ai_orchestration.initial_prompts import initial_prompt_registry
from ..ai_orchestration.provider_registry import default_registry
from .action_service import action_service
from .prompt import BASE_PROMPT
from .query_executor import QueryExecutor
from .schema_analyzer import SchemaAnalyzer
from .skill_service import skill_service


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
        configured_model = model or (
            os.getenv("COPILOT_OPENAI_MODEL", "gpt-4.1-mini") if provider == "openai"
            else os.getenv("COPILOT_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        )
        resolution = default_registry().resolve(
            requested_provider=provider, requested_model=model,
            configured_provider=provider, configured_model=configured_model,
            allow_request_override=model is not None, required_capabilities={"text", "tool_use"},
        )
        key_policy = (await db.execute(select(AIProviderKey).where(
            AIProviderKey.user_id == user_id,
            AIProviderKey.provider == resolution.effective_provider,
            AIProviderKey.is_active.is_(True),
            AIProviderKey.is_validated.is_(True),
        ))).scalar_one_or_none()
        if key_policy is None or key_policy.monthly_token_limit is None:
            raise ValueError("A validated tenant-scoped key and explicit monthly AI budget are required")
        if int(key_policy.tokens_used_month or 0) >= int(key_policy.monthly_token_limit):
            raise ValueError("Monthly AI budget exhausted")
        provider = resolution.effective_provider
        model = resolution.effective_model
        prompt_version = initial_prompt_registry().resolve("copilot", "1.0.0")
        skills = await skill_service.retrieve(db, user_id, message)
        system = BASE_PROMPT + "\nContexto da tela:\n" + json.dumps(context, ensure_ascii=False)
        if skills:
            system += "\n\nSkills recuperadas:\n" + "\n".join(
                f"- [{item['skill_type']} v{item['version']}] {item['name']}: {item['content']}" for item in skills
            )
        trace = {
            "queries": [], "evidence": [], "action_plan": None, "skills_used": skills,
            "tenant_id": str(user_id), "authority": "PROPOSAL_ONLY",
            "configured_provider": resolution.configured_provider,
            "configured_model": resolution.configured_model,
            "effective_provider": resolution.effective_provider,
            "effective_model": resolution.effective_model,
            "model_resolution_reason": resolution.resolution_reason,
            "prompt_key": prompt_version.prompt_key,
            "prompt_version": prompt_version.semantic_version,
            "prompt_hash": prompt_version.content_hash,
        }
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
        api_key = await get_decrypted_api_key(db, user_id, "anthropic")
        if not api_key:
            raise ValueError("Configure an Anthropic key in AI Integrations")
        selected_model = model or os.getenv("COPILOT_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        async def callback(name, payload):
            return await self._tool(db, user_id, session_id, name, payload, trace)
        return await CopilotProviderTransport().anthropic(
            api_key=api_key, model=selected_model, system=system, message=message, tools=TOOLS,
            tool_callback=callback, max_rounds=MAX_TOOL_ROUNDS, final_instruction=FINAL_SYNTHESIS_INSTRUCTION,
        )

    async def _run_openai(self, db, user_id, message, session_id, system, model, trace):
        api_key = await get_decrypted_api_key(db, user_id, "openai")
        if not api_key:
            raise ValueError("Configure an OpenAI key in AI Integrations")
        selected_model = model or os.getenv("COPILOT_OPENAI_MODEL", "gpt-4.1-mini")
        async def callback(name, payload):
            return await self._tool(db, user_id, session_id, name, payload, trace)
        return await CopilotProviderTransport().openai(
            api_key=api_key, model=selected_model, system=system, message=message, tools=TOOLS,
            tool_callback=callback, max_rounds=MAX_TOOL_ROUNDS, final_instruction=FINAL_SYNTHESIS_INSTRUCTION,
        )


copilot_agent = CopilotAgent()
