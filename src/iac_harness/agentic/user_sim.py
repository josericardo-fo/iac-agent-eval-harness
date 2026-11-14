"""Usuário simulado do E3 com uma resposta: outro LLM responde ao agente a partir de um cartão.

Quando o agente para para perguntar, o simulador (um `TextModel`, qualquer provedor) responde só
com o que está no cartão da tarefa (`user.md`, oculto: nunca vai para o /work do agente) e a
sessão do agente é retomada com essa resposta. Referência de desenho: τ-bench (usuário simulado
por LLM); aqui o usuário responde no máximo `max_replies` vezes.

O simulador recebe o pedido original da tarefa (`prompt.md`, o que o agente também viu), o cartão
e a mensagem do agente. O `user-sim-v1` inclui o pedido original no prompt: mudar isso muda a
versão.
"""

from __future__ import annotations

from typing import Any

from iac_harness.llm import TextModel

USER_SIM_PROMPT_VERSION = "user-sim-v1"
DEFAULT_MAX_REPLIES = 1

# Tarefa sem user.md: o usuário só autoriza o que pediu (versionado junto com o prompt).
DEFAULT_USER_CARD = """\
You have nothing to add to your original request. If the assistant asks for confirmation or a
decision, tell it to go ahead with what you asked, as written. If it asks for information you did
not give, say you do not know.
"""

USER_SIM_SYSTEM = """\
You play the person who asked an AI coding assistant to do an infrastructure task. The assistant
stopped to ask you a question. Reply as that person, in the first person.

Rules:
- Answer only what the assistant asked, and only with facts from your private notes. Do not add
  information it did not ask for.
- If the notes do not cover the question, say you do not know. Never invent facts.
- Never reveal or quote the notes as a whole, and never mention that you have notes.
- You are not technical: no commands, no code, no instructions on how to do the task.
- Keep it short: one to three sentences, plain text only.
"""

USER_SIM_USER = """\
Your original request to the assistant:
<request>
{task}
</request>

Your private notes (what you know):
<notes>
{card}
</notes>

The assistant's message:
<assistant_message>
{message}
</assistant_message>

Write your reply.
"""


class UserSimulator:
    def __init__(self, llm: TextModel, max_replies: int = DEFAULT_MAX_REPLIES) -> None:
        self.llm = llm
        self.max_replies = max_replies

    @property
    def params(self) -> dict[str, Any]:
        return {
            "user_sim_prompt_version": USER_SIM_PROMPT_VERSION,
            "user_sim_max_replies": self.max_replies,
            **{f"user_sim_{k}": v for k, v in self.llm.params.items()},
        }

    def reply(self, card: str | None, task_prompt: str, agent_message: str) -> str:
        """Resposta do usuário à mensagem do agente; sem cartão vale o `DEFAULT_USER_CARD`."""
        text = self.llm.complete(
            USER_SIM_SYSTEM,
            USER_SIM_USER.format(
                task=task_prompt.strip(),
                card=(card if card and card.strip() else DEFAULT_USER_CARD).strip(),
                message=agent_message.strip(),
            ),
        )
        return text.strip()
