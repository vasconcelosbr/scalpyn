from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class TrustLabel(StrEnum):
    TRUSTED_SYSTEM = "TRUSTED_SYSTEM"
    TRUSTED_CONFIG = "TRUSTED_CONFIG"
    TRUSTED_CALCULATED = "TRUSTED_CALCULATED"
    USER_INPUT = "USER_INPUT"
    DATABASE_UNTRUSTED_TEXT = "DATABASE_UNTRUSTED_TEXT"
    EXTERNAL_CONTENT = "EXTERNAL_CONTENT"
    MODEL_OUTPUT = "MODEL_OUTPUT"


class SanitizedValue(BaseModel):
    model_config = ConfigDict(frozen=True)
    trust_label: TrustLabel
    value: Any
    redactions: tuple[str, ...] = ()
    truncated: bool = False
    injection_neutralized: bool = False


_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*([^\s,;]+)")
_INJECTION = re.compile(r"(?i)(ignore|disregard|override).{0,40}(instructions?|system|policy)|<\s*/?\s*(system|assistant|tool)")


def sanitize(value: Any, trust_label: TrustLabel, *, max_chars: int = 8_000) -> SanitizedValue:
    if not isinstance(value, str):
        return SanitizedValue(trust_label=trust_label, value=value)
    redactions: list[str] = []
    text = _SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    if text != value:
        redactions.append("SECRET_PATTERN")
    injection = trust_label in {TrustLabel.USER_INPUT, TrustLabel.DATABASE_UNTRUSTED_TEXT, TrustLabel.EXTERNAL_CONTENT} and bool(_INJECTION.search(text))
    if injection:
        text = _INJECTION.sub("[UNTRUSTED_INSTRUCTION_REMOVED]", text)
    truncated = len(text) > max_chars
    text = text[:max_chars]
    return SanitizedValue(trust_label=trust_label, value=text, redactions=tuple(redactions),
                          truncated=truncated, injection_neutralized=injection)


def structured_block(label: TrustLabel, value: Any) -> str:
    sanitized = sanitize(value, label)
    return f"<data trust=\"{label.value}\">\n{sanitized.value}\n</data>"
