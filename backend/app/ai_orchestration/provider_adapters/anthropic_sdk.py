from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnthropicTextResponse:
    text: str
    tokens_input: int
    tokens_output: int


class AnthropicSDKTextAdapter:
    """Compatibility transport kept behind the central adapter boundary."""

    async def execute(
        self, *, api_key: str, model: str, prompt: str, max_tokens: int,
        system_prompt: str | None = None,
    ) -> AnthropicTextResponse:
        import anthropic

        request = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            request["system"] = system_prompt
        response = await anthropic.AsyncAnthropic(api_key=api_key).messages.create(**request)
        text = response.content[0].text if response.content else ""
        usage = response.usage
        return AnthropicTextResponse(
            text=text,
            tokens_input=int(usage.input_tokens if usage else 0),
            tokens_output=int(usage.output_tokens if usage else 0),
        )
