"""
Profile AI Explanation Service — uses Anthropic to generate explanations.
Only called when:
  - 'anthropic' provider key is configured and active
  - enable_anthropic_explanations = True in settings
  - Token budget allows
Falls back to quantitative explanation if unavailable.
"""
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_anthropic_key(db: AsyncSession, user_id: UUID) -> Optional[str]:
    """Retrieve decrypted Anthropic key for user, or None."""
    try:
        from ..models.ai_provider_key import AIProviderKey
        result = await db.execute(
            select(AIProviderKey).where(
                AIProviderKey.user_id == user_id,
                AIProviderKey.provider == "anthropic",
                AIProviderKey.is_active == True,
            )
        )
        rec = result.scalars().first()
        if not rec:
            return None
        from .ai_keys_service import decrypt_value
        return decrypt_value(bytes(rec.api_key_encrypted))
    except Exception as exc:
        logger.debug("[PIExplain] Could not retrieve Anthropic key: %s", exc)
        return None


class ProfileAIExplanationService:
    MODEL = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 500

    async def explain_suggestion(
        self,
        db: AsyncSession,
        user_id: UUID,
        suggestion_id: UUID,
        run_id: Optional[UUID] = None,
    ) -> str:
        """
        Generate or update AI explanation for a suggestion.
        Returns the explanation text (from Anthropic or deterministic fallback).
        """
        from ..models.profile_intelligence import ProfileSuggestion
        result = await db.execute(
            select(ProfileSuggestion).where(
                ProfileSuggestion.id == suggestion_id,
                ProfileSuggestion.user_id == user_id,
            )
        )
        suggestion = result.scalars().first()
        if not suggestion:
            return ""

        api_key = await get_anthropic_key(db, user_id)
        if not api_key:
            explanation = self._fallback_explanation(suggestion)
            suggestion.ai_explanation = explanation
            await db.flush()
            return explanation

        try:
            explanation = await self._call_anthropic(api_key, suggestion)
        except Exception as exc:
            logger.warning("[PIExplain] Anthropic call failed: %s — using fallback", exc)
            explanation = self._fallback_explanation(suggestion)

        # Update the suggestion
        suggestion.ai_explanation = explanation
        await db.flush()

        # Audit log
        from .profile_intelligence_audit_service import log_pi_event
        await log_pi_event(
            db=db,
            user_id=user_id,
            event_type="anthropic_explanation",
            event_description=f"Generated explanation for suggestion {suggestion_id}",
            run_id=run_id,
            suggestion_id=suggestion_id,
            model_provider="anthropic",
            model_name=self.MODEL,
            response_text=explanation[:2000] if explanation else None,
        )

        return explanation

    async def _call_anthropic(self, api_key: str, suggestion) -> str:
        """Call Anthropic API with sanitized metrics (no fabrication)."""
        import anthropic

        evidence = suggestion.evidence_summary_json or {}
        rules = suggestion.suggested_signals_json or {}
        quant = suggestion.quantitative_explanation or ""

        prompt = f"""Você é um analista quant de cripto. Analise esta combinação de trading e explique em português, de forma concisa (máximo 200 palavras):

Nome sugerido: {suggestion.suggested_profile_name}
Família: {suggestion.suggested_profile_family or 'desconhecida'}

Métricas quantitativas (calculadas, não invente números):
{quant}

Regras/sinais configurados:
{str(rules)[:500]}

Evidências:
{str(evidence)[:500]}

Responda:
1. Por que essa combinação pode funcionar?
2. Qual é o risco principal?
3. Existe risco de overfitting?

IMPORTANTE: Não invente métricas. Use apenas os dados fornecidos acima."""

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text if response.content else ""

    def _fallback_explanation(self, suggestion) -> str:
        """Generate deterministic explanation when Anthropic is unavailable."""
        quant = suggestion.quantitative_explanation or ""
        family = suggestion.suggested_profile_family or "unknown"
        level = suggestion.confidence_level or "LOW"
        risk = suggestion.risk_notes or ""

        explanation_parts = [
            f"[Explicação automática — Anthropic indisponível]",
            f"",
            f"Família: {family} | Confiança: {level}",
            f"",
            quant if quant else "Dados quantitativos não disponíveis.",
        ]
        if risk:
            explanation_parts.extend(["", f"Riscos: {risk}"])

        return "\n".join(explanation_parts)
