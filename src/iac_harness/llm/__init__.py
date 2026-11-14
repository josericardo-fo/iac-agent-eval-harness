"""Camada mínima de modelo de texto: `complete(system, user) -> str`, sem saber o provedor.

Quem consome (juiz, usuário simulado, outros papéis) depende só do protocolo `TextModel`; o
provedor entra pela config (`provider = "anthropic" | "codex"`) via `build_text_model`.
"""

from __future__ import annotations

from typing import Any, Protocol

PROVIDERS = ("anthropic", "codex")


class TextModel(Protocol):
    @property
    def params(self) -> dict[str, Any]:
        """Parâmetros que identificam a chamada (provedor, modelo...), para a proveniência."""
        ...

    def complete(self, system: str, user: str) -> str: ...


def build_text_model(section: dict[str, Any], default_provider: str) -> TextModel:
    """Monta o modelo da seção da config (`provider`, `model`, + opções do provedor).

    O modelo padrão é o do provedor. Provedor desconhecido levanta ValueError. Imports tardios:
    cada backend só carrega o que usa.
    """
    provider = section.get("provider", default_provider)
    if provider == "anthropic":
        from iac_harness.llm.anthropic_text import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, AnthropicText

        return AnthropicText(
            model=section.get("model", DEFAULT_MODEL),
            max_tokens=section.get("max_tokens", DEFAULT_MAX_TOKENS),
        )
    if provider == "codex":
        from iac_harness.llm.codex_cli import DEFAULT_MODEL, CodexText

        return CodexText(
            model=section.get("model", DEFAULT_MODEL),
            reasoning_effort=section.get("reasoning_effort"),
        )
    raise ValueError(f"provider desconhecido: {provider!r} (use um de {list(PROVIDERS)})")
