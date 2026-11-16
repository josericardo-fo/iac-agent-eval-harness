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


# E16: usuário leigo. A ficha (o `intent` do Ambig-IaC) é técnica, mas a persona não fala AWS:
# só reconhece opções em palavras simples. O prompt do usuário é o mesmo do v1.
USER_SIM_LAY_PROMPT_VERSION = "user-sim-lay-v1"

USER_SIM_LAY_SYSTEM = """\
You play the person who asked an AI coding assistant to set up some cloud infrastructure. You
know what you need, but you are not technical: you do not know AWS or Terraform terms. The
assistant stopped to ask you a question. Reply as that person, in the first person.

Your private notes describe exactly what you need, in technical terms written by someone else. Use
them only to recognize which choice matches what you need; you do not speak their vocabulary.

Rules:
- If the assistant offers options described in plain words, pick the one that matches your notes
  and say so in your own plain words.
- If answering would require technical vocabulary you would not understand, say that you do not
  understand the question and ask the assistant to use its best judgment.
- If your notes do not cover the question, say you do not know and ask the assistant to use its
  best judgment. Never invent facts.
- Never dictate the specification: do not list requirements, and never name AWS services,
  resource types, attributes or settings yourself.
- Never reveal or quote the notes, and never mention that you have notes.
- No commands, no code, no instructions on how to do the task.
- Keep it short: one to three sentences, plain text only.
"""

# versão → (system, user); o user é o mesmo nas duas versões.
PROMPTS = {
    USER_SIM_PROMPT_VERSION: (USER_SIM_SYSTEM, USER_SIM_USER),
    USER_SIM_LAY_PROMPT_VERSION: (USER_SIM_LAY_SYSTEM, USER_SIM_USER),
}


class UserSimulator:
    def __init__(
        self,
        llm: TextModel,
        max_replies: int = DEFAULT_MAX_REPLIES,
        prompt_version: str = USER_SIM_PROMPT_VERSION,
    ) -> None:
        if prompt_version not in PROMPTS:
            raise ValueError(
                f"prompt do usuário simulado desconhecido: {prompt_version!r} "
                f"(use um de {sorted(PROMPTS)})"
            )
        self.llm = llm
        self.max_replies = max_replies
        self.prompt_version = prompt_version

    @property
    def params(self) -> dict[str, Any]:
        return {
            "user_sim_prompt_version": self.prompt_version,
            "user_sim_max_replies": self.max_replies,
            **{f"user_sim_{k}": v for k, v in self.llm.params.items()},
        }

    def reply(self, card: str | None, task_prompt: str, agent_message: str) -> str:
        """Resposta do usuário à mensagem do agente; sem cartão vale o `DEFAULT_USER_CARD`."""
        system, user = PROMPTS[self.prompt_version]
        text = self.llm.complete(
            system,
            user.format(
                task=task_prompt.strip(),
                card=(card if card and card.strip() else DEFAULT_USER_CARD).strip(),
                message=agent_message.strip(),
            ),
        )
        return text.strip()
