"""Backend Anthropic. Único módulo que importa o SDK; sem retries e sem temperature."""

from __future__ import annotations

import re
from typing import Any

from anthropic import Anthropic

from iac_harness.agents.prompts import (
    PROMPT_VERSION,
    REPAIR_TEMPLATE,
    SYSTEM_PROMPT,
    render_files,
    render_repair,
)
from iac_harness.types import AgentReply, Artifact, Task, Turn, Usage

DEFAULT_MAX_TOKENS = 8192

# ```hcl title="main.tf" ... ```  (aceita também filename= e aspas simples)
_BLOCK = re.compile(
    r"```[\w-]*[ \t]+(?:title|filename|file)\s*=\s*[\"']([^\"'\n]+)[\"'][^\n]*\n(.*?)```",
    re.DOTALL,
)


def parse_files(text: str) -> dict[str, str]:
    """Extrai os arquivos dos blocos nomeados; em nome repetido, o último vence."""
    return {name.strip(): body for name, body in _BLOCK.findall(text)}


def build_messages(
    task: Task,
    history: list[Turn],
    repair_template: str = REPAIR_TEMPLATE,
    code_lang: str = "hcl",
) -> list[dict[str, str]]:
    """Histórico: prompt cru, e para cada turno (código anterior + diagnóstico) o repair."""
    messages = [{"role": "user", "content": task.prompt}]
    for turn in history:
        files = turn.artifact.files
        previous = turn.raw_text if turn.raw_text is not None else render_files(files, code_lang)
        messages.append({"role": "assistant", "content": previous})
        messages.append(
            {
                "role": "user",
                "content": render_repair(files, turn.feedback, repair_template, code_lang),
            }
        )
    return messages


class AnthropicBackend:
    def __init__(
        self,
        model: str = "claude-sonnet-5-5",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
        client: Any | None = None,
        system_prompt: str | None = None,
        repair_template: str | None = None,
        prompt_version: str | None = None,
        code_lang: str = "hcl",
    ) -> None:
        """Sem `system_prompt`/`repair_template`, usa os prompts do A/B/C (PROMPT_VERSION).

        Prompt customizado exige `prompt_version`, para o params registrar a versão usada.
        """
        if (system_prompt or repair_template) and not prompt_version:
            raise ValueError("prompt customizado exige prompt_version")
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.repair_template = repair_template or REPAIR_TEMPLATE
        self.prompt_version = prompt_version or PROMPT_VERSION
        self.code_lang = code_lang  # linguagem dos blocos de código reconstruídos no histórico
        # max_retries=0: retries escondidos contaminariam a variável experimental.
        self._client = client if client is not None else Anthropic(max_retries=0)

    @property
    def params(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "effort": self.effort,
            "temperature": None,
            "max_retries": 0,
            "prompt_version": self.prompt_version,
        }

    def generate(self, task: Task, history: list[Turn]) -> AgentReply:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system_prompt,
            "messages": build_messages(task, history, self.repair_template, self.code_lang),
        }
        if self.effort is not None:
            kwargs["output_config"] = {"effort": self.effort}
        response = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        return AgentReply(
            artifact=Artifact(parse_files(text)),  # vazio se não houver bloco parseável
            usage=Usage(response.usage.input_tokens, response.usage.output_tokens),
            model_id=response.model,
            request_id=getattr(response, "_request_id", None),
            raw_text=text,
            params=self.params,
        )
