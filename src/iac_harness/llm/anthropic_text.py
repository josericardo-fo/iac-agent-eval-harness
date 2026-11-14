"""Backend Anthropic (gateway em `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`) do `TextModel`."""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic

DEFAULT_MODEL = "Sonnet 5.5"  # ID aceito pelo gateway
DEFAULT_MAX_TOKENS = 1024


class AnthropicText:
    provider = "anthropic"

    def __init__(
        self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:  # fmt: skip
        self.model = model
        self.max_tokens = max_tokens
        self._client = client if client is not None else Anthropic()

    @property
    def params(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "max_tokens": self.max_tokens}

    def complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
