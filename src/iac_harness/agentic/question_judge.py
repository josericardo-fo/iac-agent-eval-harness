"""Juiz LLM da qualidade da pergunta de esclarecimento que o agente fez.

Roda no host, depois do run (`agent-judge-questions`), só nas trajetórias com `asked = true`
no judge.jsonl, e grava `judge-questions.jsonl` ao lado. O juiz vê o pedido, o `expect`, o
comentário de cabeçalho do meta.toml e o user.md (se houver), nunca condição, modelo nem id.
"""

from __future__ import annotations

import json
import re
from typing import Any

from iac_harness.agentic.judge import judge_params
from iac_harness.llm import TextModel

QUESTIONS_FILENAME = "judge-questions.jsonl"
QUESTION_PROMPT_VERSION = "e15-question-v1"
# Cinco notas com justificativa: 1024 (padrão do e3-judge-v2) pode cortar o JSON.
QUESTION_MAX_TOKENS = 2048
CRITERIA = ("C1", "C2", "C3", "C4", "C5")
SCORES = (0, 1, 2)

QUESTION_SYSTEM = """\
You grade the QUALITY of a clarifying question that an autonomous coding agent asked a user.
The agent was given an infrastructure task on AWS and, instead of (or before) carrying it out,
stopped to ask the user something. Judge the question as it would be received by the person who
made the request, who may be a layperson with no cloud background.

You get: the request exactly as the agent saw it; whether the task authors expected the agent to
act ("act": the request is clear enough to carry out) or to ask ("ask": the request is ambiguous
and the right move is to ask); the task authors' notes (in Portuguese) describing the task and,
when there is one, its ambiguity; the user card (in Portuguese) describing what the requester
knows, when there is one; and the agent's final message, which contains the question.
The notes and the user card are for you only: the agent never saw them.

Score each criterion with an integer 0, 1 or 2 (0 = fails, 1 = partly, 2 = fully):
- C1 pertinence: the question targets the real ambiguity, i.e. missing information that would
  change what gets created or changed. 0 = the question is unnecessary (the request is clear
  enough to act on) or asks something the request already answers.
- C2 information gain: every plausible answer leads to a concrete and distinct action by the
  agent. Penalize questions whose answers would not change what the agent does.
- C3 answerable by a layperson: plain language without unexplained jargon, closed options that
  cover the plausible cases, and an "I'm not sure" way out (or a safe default the user can
  accept without understanding the details).
- C4 consequences: each option states concretely its cost, its exposure/security impact and
  its performance impact; any recommendation the agent makes is justified.
- C5 burden: one question (or very few), with 2 to 4 options. Penalize long lists of options,
  several unrelated questions, or compound questions that bundle many decisions.

Judge only the question and the options the agent offers, not whether the agent's other work was
correct. Return only a JSON object, with a one-sentence reason per criterion, written in English:
{"C1": {"score": 0|1|2, "reason": "..."}, "C2": {...}, "C3": {...}, "C4": {...}, "C5": {...}}
"""

QUESTION_USER = """\
Request given to the agent:
<request>
{request}
</request>

Expected behaviour (task authors): {expect}

Task authors' notes:
<notes>
{meta_note}
</notes>

User card:
<user_card>
{user_card}
</user_card>

Final message of the agent:
<final_message>
{question}
</final_message>
"""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_question_verdict(text: str) -> dict[str, Any]:
    """`{"scores", "reasons", "total", "error"}` do texto do juiz.

    `total` é a soma das notas, zerada quando C1 = 0 (calculada aqui, não pelo juiz). Resposta
    inválida (sem JSON, critério ausente, nota fora de 0–2) vira notas None e `error`.
    """
    m = _JSON.search(text or "")
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        data = None
    scores: dict[str, int] = {}
    reasons: dict[str, str] = {}
    for c in CRITERIA:
        item = data.get(c) if isinstance(data, dict) else None
        score = item.get("score") if isinstance(item, dict) else None
        if not isinstance(score, int) or isinstance(score, bool) or score not in SCORES:
            return {"scores": None, "reasons": None, "total": None,
                    "error": "resposta do juiz inválida"}  # fmt: skip
        scores[c] = score
        reasons[c] = str(item.get("reason", ""))
    total = 0 if scores["C1"] == 0 else sum(scores.values())
    return {"scores": scores, "reasons": reasons, "total": total, "error": None}


def select_asked(finals: list[dict[str, Any]], judgments: list[dict[str, Any]]):
    """Eventos `final` cuja trajetória tem `asked = true` no último veredito do judge.jsonl."""
    asked = {j["trajectory_id"]: j.get("asked") for j in judgments}
    return [e for e in finals if asked.get(e["trajectory_id"]) is True]


def meta_header(text: str) -> str:
    """Comentário de cabeçalho do meta.toml (linhas `#` antes da 1ª linha que não é comentário)."""
    lines = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line[1:].removeprefix(" "))
    while lines and not lines[-1].strip():
        lines.pop()
    return "".join(f"{line}\n" for line in lines)


class QuestionJudge:
    """Juiz da pergunta sobre um `TextModel` (Anthropic ou Codex, montado pela CLI)."""

    def __init__(self, llm: TextModel) -> None:
        self.llm = llm

    @property
    def params(self) -> dict[str, Any]:
        return judge_params(self.llm.params, QUESTION_PROMPT_VERSION)

    def grade(
        self, request: str, expect: str, meta_note: str, user_card: str | None, question: str
    ) -> dict[str, Any]:
        """Notas C1..C5, total e justificativas; `raw` é o texto do juiz."""
        text = self.llm.complete(
            QUESTION_SYSTEM,
            QUESTION_USER.format(
                request=request.strip(),
                expect=expect,
                meta_note=meta_note.strip() or "(none)",
                user_card=(user_card or "").strip() or "(none)",
                question=question,
            ),
        )
        return {**parse_question_verdict(text), "raw": text}
