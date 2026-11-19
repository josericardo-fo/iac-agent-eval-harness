"""Leitura do stream-json do `claude -p --output-format stream-json --verbose`.

Eventos usados: `system/init` (plugins e ferramentas carregados), mensagens `assistant` com blocos
`tool_use` (chamadas de ferramenta) e o `result` final (texto, usage, custo, turnos).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedStream:
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # {"name", "input"}

    @property
    def final_text(self) -> str | None:
        return self.result.get("result") if self.result else None

    @property
    def usage(self) -> dict[str, Any]:
        return dict(self.result.get("usage") or {}) if self.result else {}

    @property
    def cost_usd(self) -> float | None:
        return self.result.get("total_cost_usd") if self.result else None

    @property
    def num_turns(self) -> int | None:
        return self.result.get("num_turns") if self.result else None

    @property
    def is_error(self) -> bool | None:
        return self.result.get("is_error") if self.result else None

    @property
    def subtype(self) -> str | None:
        return self.result.get("subtype") if self.result else None

    @property
    def permission_denials(self) -> list[Any]:
        return list(self.result.get("permission_denials") or []) if self.result else []

    @property
    def tool_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(c["name"] for c in self.tool_calls).items()))


# Valores menores que isto não são filtrados (ex.: "test" das credenciais do Floci).
MIN_SECRET_LEN = 8
REDACTED = "***"


def redact(text: str, secrets: Iterable[str | None]) -> str:
    """Troca cada valor secreto por `***` (o token do gateway fica visível dentro do container)."""
    for value in sorted({s for s in secrets if s and len(s) >= MIN_SECRET_LEN}, key=len,
                        reverse=True):  # fmt: skip
        text = text.replace(value, REDACTED)
    return text


def parse_stream(lines: Iterable[str]) -> ParsedStream:
    """Linhas inválidas (ex.: aviso no stdout, linha truncada por timeout) são ignoradas."""
    out = ParsedStream()
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out.init = event
        elif kind == "result":
            out.result = event
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    out.tool_calls.append(
                        {"name": block.get("name", ""), "input": block.get("input") or {}}
                    )
    return out
