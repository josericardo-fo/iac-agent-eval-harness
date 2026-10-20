"""Prompts do teste de modalidades, versionados. Mudar qualquer texto exige subir PROMPT_VERSION.

Um prompt-base comum e um bloco curto e simétrico por modalidade (desenho §5): mesmo nível de
detalhe nas três (como o arquivo é executado e versões, o que está disponível e a restrição de
ferramenta).
"""

from __future__ import annotations

from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import Modality

PROMPT_VERSION = "mod-v2"

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


def system_prompt(modality: Modality) -> str:
    return BASE_SYSTEM_PROMPT.format(
        modality_block=MODALITY_BLOCKS[modality.name],
        filename=modality.filename,
        lang=CODE_LANG[modality.name],
    )


def repair_template(modality: Modality) -> str:
    """Template no formato de agents.prompts.render_repair (só `message` é usado)."""
    return REPAIR_TEMPLATE.replace("{filename}", modality.filename)


def user_prompt(task: ModalityTask, modality: Modality) -> str:
    """Enunciado; nas tarefas de modificação, acrescenta o initial.tf e a frase da modalidade."""
    if task.initial_tf is None:
        return task.prompt
    return MODIFY_TEMPLATE.format(
        prompt=task.prompt.rstrip(),
        initial=task.initial_tf.rstrip(),
        note=MODIFY_NOTES[modality.name],
    )
