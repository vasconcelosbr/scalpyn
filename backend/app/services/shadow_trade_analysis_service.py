"""Provider-neutral structured AI analysis for Shadow Portfolio samples."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


SYSTEM_PROMPT = """Você é um auditor quantitativo de Shadow Trades do Scalpyn.
Use somente os dados recebidos. Não invente métricas, não confunda associação com
causalidade e não recomende alteração sem citar trade_ids. Responda somente JSON
válido, sem markdown, no schema solicitado. Recomendações podem cobrir filters,
scoring, signals, block_rules e entry_triggers, mas nunca nome, id, versão, role,
pipeline ou flags live do profile. Todo score precisa listar selected_rule_ids e,
quando necessário, score_matrix_patch com regras globais completas."""

OUTPUT_SCHEMA = {
    "summary": "string",
    "sample": {"trade_count": "integer", "tp_count": "integer", "sl_count": "integer"},
    "observations": [{"fact": "string", "evidence_trade_ids": ["uuid"]}],
    "data_quality": [{"issue": "string", "affected_trade_ids": ["uuid"]}],
    "recommendations": [
        {
            "profile_id": "uuid",
            "profile_name": "string",
            "rationale": "string",
            "evidence_trade_ids": ["uuid"],
            "changes": [
                {
                    "op": "add|replace|remove",
                    "path": "/signals/conditions/0/value",
                    "old_value": None,
                    "value": None,
                    "reason": "string",
                    "evidence_refs": ["uuid"],
                }
            ],
            "score_matrix_patch": {"upsert_rules": [], "remove_rule_ids": []},
            "score_assignment": {"selected_rule_ids": []},
        }
    ],
    "limitations": ["string"],
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object")
    for key in ("summary", "sample", "observations", "data_quality", "recommendations", "limitations"):
        if key not in parsed:
            raise ValueError(f"AI response missing required field: {key}")
    if not isinstance(parsed["recommendations"], list):
        raise ValueError("AI recommendations must be an array")
    return parsed


async def _call_provider(provider: str, api_key: str, model: str, prompt: str) -> tuple[str, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
        if provider == "openai":
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            return payload["choices"][0]["message"]["content"], payload.get("usage") or {}
        if provider == "anthropic":
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 6000,
                    "temperature": 0,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
            text = "\n".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
            return text, payload.get("usage") or {}
        if provider == "gemini":
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                json={
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                },
            )
            response.raise_for_status()
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            return text, payload.get("usageMetadata") or {}
    raise ValueError(f"Unsupported AI provider: {provider}")


def _chunks(documents: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    max_chars = int(os.getenv("SHADOW_ANALYSIS_MAX_INPUT_CHARS", "300000"))
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for document in documents:
        document_size = len(json.dumps(document, ensure_ascii=False, default=str))
        if current and size + document_size > max_chars:
            chunks.append(current)
            current = []
            size = 0
        current.append(document)
        size += document_size
    if current:
        chunks.append(current)
    return chunks


async def analyze_trade_documents(
    *,
    provider: str,
    api_key: str,
    model: str,
    documents: list[dict[str, Any]],
    selection: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if not documents:
        raise ValueError("Analysis sample is empty")
    analyses: list[dict[str, Any]] = []
    raw_parts: list[str] = []
    usages: list[dict[str, Any]] = []
    for index, chunk in enumerate(_chunks(documents), start=1):
        prompt = json.dumps(
            {
                "task": "analyze_shadow_trades",
                "selection": selection,
                "batch": {"index": index, "trade_count": len(chunk)},
                "required_output_schema": OUTPUT_SCHEMA,
                "trades": chunk,
            },
            ensure_ascii=False,
            default=str,
        )
        raw, usage = await _call_provider(provider, api_key, model, prompt)
        raw_parts.append(raw)
        usages.append(usage)
        analyses.append(_extract_json(raw))

    if len(analyses) == 1:
        return analyses[0], raw_parts[0], {"provider_calls": usages}

    synthesis_prompt = json.dumps(
        {
            "task": "synthesize_shadow_trade_batches_without_losing_evidence",
            "selection": selection,
            "required_output_schema": OUTPUT_SCHEMA,
            "batch_analyses": analyses,
        },
        ensure_ascii=False,
        default=str,
    )
    raw, usage = await _call_provider(provider, api_key, model, synthesis_prompt)
    raw_parts.append(raw)
    usages.append(usage)
    return _extract_json(raw), "\n\n".join(raw_parts), {"provider_calls": usages}
