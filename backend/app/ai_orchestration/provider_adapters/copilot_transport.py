from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import httpx


ToolCallback = Callable[[str, dict[str, Any]], Awaitable[Any]]


class CopilotProviderTransport:
    """Central transport for Co-Pilot; tool authority remains code-enforced."""

    async def anthropic(self, *, api_key: str, model: str, system: str, message: str,
                        tools: list[dict], tool_callback: ToolCallback, max_rounds: int,
                        final_instruction: str) -> str:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        for _ in range(max_rounds):
            response = await client.messages.create(model=model, max_tokens=3000, system=system, tools=tools, messages=messages)
            blocks = [block.model_dump() if hasattr(block, "model_dump") else block for block in response.content]
            messages.append({"role": "assistant", "content": blocks})
            uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            if not uses:
                return "\n".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text").strip()
            results = []
            for use in uses:
                try:
                    value = await tool_callback(use.name, dict(use.input))
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": json.dumps(value, ensure_ascii=False, default=str)})
                except Exception as exc:
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": f"{type(exc).__name__}: {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
        response = await client.messages.create(model=model, max_tokens=3000, system=system + final_instruction, messages=messages)
        return "\n".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text").strip()

    async def openai(self, *, api_key: str, model: str, system: str, message: str,
                     tools: list[dict], tool_callback: ToolCallback, max_rounds: int,
                     final_instruction: str) -> str:
        api_tools = [{"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                      "parameters": tool["input_schema"]}} for tool in tools]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": message}]
        async with httpx.AsyncClient(timeout=90) as client:
            for _ in range(max_rounds):
                response = await client.post("https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "messages": messages, "tools": api_tools, "tool_choice": "auto"})
                response.raise_for_status()
                assistant = response.json()["choices"][0]["message"]
                messages.append(assistant)
                calls = assistant.get("tool_calls") or []
                if not calls:
                    return assistant.get("content") or ""
                for call in calls:
                    try:
                        value = await tool_callback(call["function"]["name"], json.loads(call["function"]["arguments"] or "{}"))
                        content = json.dumps(value, ensure_ascii=False, default=str)
                    except Exception as exc:
                        content = f"{type(exc).__name__}: {exc}"
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            messages[0]["content"] += final_instruction
            response = await client.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": messages, "tools": api_tools, "tool_choice": "none"})
            response.raise_for_status()
            return response.json()["choices"][0]["message"].get("content") or ""
