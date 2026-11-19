"""Camada mínima de modelo de texto: `complete(system, user) -> str`, sem saber o provedor.

Quem consome (juiz, usuário simulado, outros papéis) depende só do protocolo `TextModel`; o
provedor entra pela config (`provider`, `model`) via `build_text_model`, sem padrão escondido.
Provedor novo: um módulo com `DEFAULT_MODEL` e `from_section(section)`, mais uma linha em
`PROVIDERS`.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any, Protocol

# nome → módulo do backend (import tardio: cada backend só carrega o que usa)
PROVIDERS = {
    "anthropic": "iac_harness.llm.anthropic_text",
    "codex": "iac_harness.llm.codex_cli",
}
REQUIRED_KEYS = ("provider", "model")


class TextModel(Protocol):
    @property
    def params(self) -> dict[str, Any]:
        """Parâmetros que identificam a chamada (provedor, modelo...), para a proveniência."""
        ...

    def complete(self, system: str, user: str) -> str: ...


def _provider_module(provider: str) -> ModuleType:
    if provider not in PROVIDERS:
        raise ValueError(f"provider desconhecido: {provider!r} (use um de {list(PROVIDERS)})")
    return importlib.import_module(PROVIDERS[provider])


def build_text_model(section: dict[str, Any]) -> TextModel:
    """Monta o modelo da seção da config (`provider`, `model`, + opções do provedor).

    `provider` e `model` são obrigatórios; chave ausente ou provedor desconhecido levanta
    ValueError.
    """
    for key in REQUIRED_KEYS:
        if key not in section:
            raise ValueError(f"falta a chave {key!r} (obrigatória: {', '.join(REQUIRED_KEYS)})")
    return _provider_module(section["provider"]).from_section(section)


def default_model(provider: str) -> str:
    """Modelo padrão do provedor (só para a troca explícita de provedor na CLI, `--provider`)."""
    return _provider_module(provider).DEFAULT_MODEL
