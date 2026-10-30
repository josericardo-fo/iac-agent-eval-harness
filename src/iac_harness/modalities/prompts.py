"""Prompts do teste de modalidades, versionados. Mudar qualquer texto exige subir PROMPT_VERSION.

Um prompt-base comum e um bloco curto e simétrico por modalidade (desenho §5): mesmo nível de
detalhe nas três (como o arquivo é executado e versões, o que está disponível e a restrição de
ferramenta).
"""

from __future__ import annotations

from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import Modality

PROMPT_VERSION = "mod-v3"
# Versão com a opção de perguntar (E2): o mod-v3 mais a frase ASK_NOTE no prompt de sistema.
ASK_PROMPT_VERSION = f"{PROMPT_VERSION}-ask"
QUESTION_FILENAME = "question.md"

# Linguagem do bloco de código de cada modalidade (resposta e histórico).
CODE_LANG = {"terraform": "hcl", "cli": "bash", "boto3": "python"}

BASE_SYSTEM_PROMPT = """\
You are an infrastructure engineer for AWS.
Write code that does exactly what the user asks for.

Execution environment: the AWS endpoint and credentials are already configured in the
environment. Do not set endpoints or credentials in your code.

{modality_block}

Response format:
- Deliver the file {filename} in a single fenced code block, with the file name on the opening
  line, in this format:

```{lang} title="{filename}"
# file contents
```

- Deliver the complete contents of the file (never snippets).
- Do not use resources or local files that are not part of the request.
- Outside the block, write at most a short explanation.
"""

MODALITY_BLOCKS = {
    "terraform": (
        "Tool: Terraform. Your file main.tf is executed with `terraform init` followed by\n"
        "`terraform apply -auto-approve`, with Terraform 1.15. Available: the providers\n"
        "hashicorp/aws ~> 6.0, hashicorp/archive and hashicorp/random (no network to download\n"
        "others). Use only Terraform, with required_providers compatible with these versions."
    ),
    "cli": (
        "Tool: AWS CLI. Your file run.sh is executed with `bash -euo pipefail run.sh`, with\n"
        "AWS CLI v2 (2.38). Available: the AWS CLI v2, jq and the standard bash and coreutils\n"
        "commands. Use only these tools (no Terraform, SDKs or other tools)."
    ),
    "boto3": (
        "Tool: boto3. Your file main.py is executed with `python3 main.py`, with Python 3.11\n"
        "and boto3 1.43. Available: boto3 and the Python standard library (no pip installs).\n"
        "Use only boto3 and the Python standard library."
    ),
}

# Frase das tarefas de modificação, depois do initial.tf (mesma estrutura nas três).
MODIFY_NOTES = {
    "terraform": (
        "Edit the existing main.tf above and deliver the complete edited file. The current\n"
        "Terraform state is already in the working directory."
    ),
    "cli": (
        "These resources already exist in the account. Deliver run.sh that acts on the existing\n"
        "resources."
    ),
    "boto3": (
        "These resources already exist in the account. Deliver main.py that acts on the existing\n"
        "resources."
    ),
}

# Frase das tarefas de consulta: só diz onde sai a resposta (mesma estrutura nas três).
QUERY_NOTES = {
    "terraform": (
        'Deliver the answer as the value of a Terraform output named "answer", declared in\n'
        "main.tf; the harness reads it after the apply."
    ),
    "cli": (
        "Deliver the answer by writing it to the file answer.json in the working directory;\n"
        "the harness reads it after run.sh."
    ),
    "boto3": (
        "Deliver the answer by writing it to the file answer.json in the working directory;\n"
        "the harness reads it after main.py."
    ),
}

QUERY_TEMPLATE = """\
{prompt}

{note}
"""

MODIFY_TEMPLATE = """\
{prompt}

Existing environment (already applied in the account), described by this Terraform
configuration:

```hcl title="main.tf"
{initial}
```

{note}
"""

# O código anterior já está na mensagem do assistente; o repair traz só o erro.
REPAIR_TEMPLATE = """\
The execution failed with the error below. Deliver the complete corrected file, in the same
block format with title="{filename}".

Error:

{message}
"""

FORMAT_ERROR = (
    'Your response did not contain a fenced code block with title="{filename}", so nothing was '
    "executed."
)


# Frase neutra, sem exemplos, só com `allow_question` (entra depois do formato de resposta).
ASK_NOTE = """\
If you cannot tell what the user wants, ask instead of guessing: deliver your question in a
single fenced block with title="{question}" instead of {filename}. Nothing will be executed.
"""


# ---------- forma livre (E4) ----------
# Versão com a escolha da ferramenta: os três blocos, sem falar de custo (decisão P5).
FREE_PROMPT_VERSION = f"{PROMPT_VERSION}-free"
FREE_ORDER = ("terraform", "cli", "boto3")  # ordem fixa dos blocos no prompt

FREE_SYSTEM_PROMPT = """\
You are an infrastructure engineer for AWS.
Write code that does exactly what the user asks for.

Execution environment: the AWS endpoint and credentials are already configured in the
environment. Do not set endpoints or credentials in your code.

You may use any one of the three tools below; choose one for each response.

{modality_blocks}

Response format:
- Deliver exactly one file, the one for the tool you chose (main.tf, run.sh or main.py), in a
  single fenced code block, with the file name on the opening line, in this format (hcl and
  main.tf for Terraform, bash and run.sh for the AWS CLI, python and main.py for boto3):

```<language> title="<file name>"
# file contents
```

- Deliver the complete contents of the file (never snippets).
- Do not use resources or local files that are not part of the request.
- Outside the block, write at most a short explanation.
"""

# As frases de MODIFY_NOTES e QUERY_NOTES unidas, uma por ferramenta.
FREE_MODIFY_NOTE = (
    "If you use Terraform, edit the existing main.tf above and deliver the complete edited file;\n"
    "the current Terraform state is already in the working directory. If you use the AWS CLI or\n"
    "boto3, these resources already exist in the account: deliver run.sh or main.py that acts on\n"
    "the existing resources."
)

FREE_QUERY_NOTE = (
    'If you use Terraform, deliver the answer as the value of a Terraform output named "answer",\n'
    "declared in main.tf; the harness reads it after the apply. If you use the AWS CLI or boto3,\n"
    "deliver the answer by writing it to the file answer.json in the working directory; the\n"
    "harness reads it after run.sh or main.py."
)

FREE_REPAIR_TEMPLATE = """\
The execution failed with the error below. Deliver the complete corrected file (main.tf, run.sh
or main.py), in the same block format with title= and the file name.

Error:

{message}
"""

FREE_FORMAT_ERROR = (
    'Your response did not contain exactly one fenced code block with title="main.tf", '
    'title="run.sh" or title="main.py", so nothing was executed.'
)


def free_system_prompt() -> str:
    blocks = "\n\n".join(MODALITY_BLOCKS[name] for name in FREE_ORDER)
    return FREE_SYSTEM_PROMPT.format(modality_blocks=blocks)


def free_user_prompt(task: ModalityTask) -> str:
    """Enunciado da forma livre: mesma lógica de user_prompt, com as frases unidas."""
    return _user_prompt(task, FREE_MODIFY_NOTE, FREE_QUERY_NOTE)


def prompt_version(allow_question: bool = False, free: bool = False) -> str:
    if free:
        return FREE_PROMPT_VERSION
    return ASK_PROMPT_VERSION if allow_question else PROMPT_VERSION


def system_prompt(modality: Modality, allow_question: bool = False) -> str:
    text = BASE_SYSTEM_PROMPT.format(
        modality_block=MODALITY_BLOCKS[modality.name],
        filename=modality.filename,
        lang=CODE_LANG[modality.name],
    )
    if allow_question:
        text += "\n" + ASK_NOTE.format(question=QUESTION_FILENAME, filename=modality.filename)
    return text


def repair_template(modality: Modality) -> str:
    """Template no formato de agents.prompts.render_repair (só `message` é usado)."""
    return REPAIR_TEMPLATE.replace("{filename}", modality.filename)


def user_prompt(task: ModalityTask, modality: Modality) -> str:
    """Enunciado; em modify/delete acrescenta o initial.tf e a frase da modalidade.

    Na consulta, só a frase de onde sai a resposta: o initial.tf entregaria a resposta.
    Com `hide_initial`, só o enunciado (mais a frase da resposta, se `answer`).
    """
    return _user_prompt(task, MODIFY_NOTES[modality.name], QUERY_NOTES[modality.name])


def _user_prompt(task: ModalityTask, modify_note: str, query_note: str) -> str:
    shows_initial = task.initial_tf is not None and not task.hide_initial
    if task.category == "query" or (task.answer and not shows_initial):
        return QUERY_TEMPLATE.format(prompt=task.prompt.rstrip(), note=query_note)
    if not shows_initial:
        return task.prompt
    note = modify_note
    if task.wants_answer:
        note += "\n\n" + query_note
    return MODIFY_TEMPLATE.format(
        prompt=task.prompt.rstrip(), initial=task.initial_tf.rstrip(), note=note
    )
