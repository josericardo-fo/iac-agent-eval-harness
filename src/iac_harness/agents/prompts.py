"""Prompts do agente, versionados. Mudar qualquer texto exige subir PROMPT_VERSION."""

from __future__ import annotations

from iac_harness.types import Feedback

PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """\
You are an infrastructure engineer specialized in Terraform for AWS.
Write Terraform code that satisfies exactly what the user asks for.

Execution environment: Terraform >= 1.15 and the hashicorp/aws provider version ~> 6.0
(hashicorp/archive and hashicorp/random are also available). Declare required_providers
compatible with these versions.

Response format:
- Deliver each file in a fenced code block, with the file name on the opening line,
  in this format:

```hcl title="main.tf"
# file contents
```

- Use one block per file and deliver the complete contents of each file (never snippets).
- Do not use resources or local files that are not part of the request.
- Outside the blocks, write at most a short explanation.
"""

REPAIR_TEMPLATE = """\
The Terraform code you delivered previously did not pass verification.

Previous code:

{previous_code}

Diagnostic ({stage}, {error_class}):

{message}

Fix the problem and deliver the complete, corrected version of every file, in the same block
format with title="file-name".
"""


def render_files(files: dict[str, str]) -> str:
    return "\n\n".join(
        f'```hcl title="{name}"\n{body.rstrip()}\n```' for name, body in files.items()
    )


def render_repair(files: dict[str, str], feedback: Feedback) -> str:
    return REPAIR_TEMPLATE.format(
        previous_code=render_files(files),
        stage=feedback.stage.value,
        error_class=feedback.error_class,
        message=feedback.message,
    )
