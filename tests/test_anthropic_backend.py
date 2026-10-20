from types import SimpleNamespace

from iac_harness.agents.anthropic_backend import AnthropicBackend, build_messages, parse_files
from iac_harness.agents.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from iac_harness.types import Artifact, Feedback, Stage, Task, Turn

TEXT = """Segue:

```hcl title="main.tf"
resource "aws_s3_bucket" "b" {}
```

```hcl title="variables.tf"
variable "x" {}
```
"""


class FakeMessages:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            model="claude-sonnet-5-5",
            _request_id="req_123",
        )


def _backend(text: str, **kw):
    client = SimpleNamespace(messages=FakeMessages(text))
    return AnthropicBackend(client=client, **kw), client.messages


TASK = Task("t1", "# Task\n\n## Intent\n\nHas a bucket", 1)


def test_parse_files():
    files = parse_files(TEXT)
    assert set(files) == {"main.tf", "variables.tf"}
    assert files["main.tf"].strip() == 'resource "aws_s3_bucket" "b" {}'


def test_generate_records_usage_request_id_and_params():
    backend, messages = _backend(TEXT, max_tokens=1000)
    reply = backend.generate(TASK, [])
    assert reply.usage.input_tokens == 11 and reply.usage.output_tokens == 7
    assert reply.request_id == "req_123"
    assert reply.params == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 1000,
        "effort": None,
        "temperature": None,
        "max_retries": 0,
        "prompt_version": PROMPT_VERSION,
    }
    assert "temperature" not in messages.kwargs
    assert "output_config" not in messages.kwargs
    assert messages.kwargs["system"] == SYSTEM_PROMPT
    assert messages.kwargs["messages"] == [{"role": "user", "content": TASK.prompt}]


def test_effort_only_sent_when_configured():
    backend, messages = _backend(TEXT, effort="high")
    backend.generate(TASK, [])
    assert messages.kwargs["output_config"] == {"effort": "high"}
    assert "temperature" not in messages.kwargs


def test_history_is_built_from_turns():
    turn = Turn(Artifact({"main.tf": "bad"}), Feedback(Stage.VALIDATE, "Syntax", "Error: boom"))
    msgs = build_messages(TASK, [turn])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert "bad" in msgs[1]["content"]
    assert "Error: boom" in msgs[2]["content"] and "VALIDATE" in msgs[2]["content"]


def test_unparseable_reply_gives_empty_artifact():
    backend, _ = _backend("Não consegui gerar nada.")
    reply = backend.generate(TASK, [])
    assert reply.artifact.files == {}
    assert reply.raw_text == "Não consegui gerar nada."


def test_default_prompts_unchanged_for_abc():
    """Sem system_prompt/repair_template, o backend manda exatamente o mesmo do A/B/C."""
    from iac_harness.agents.prompts import REPAIR_TEMPLATE, render_repair

    backend, messages = _backend(TEXT)
    turn = Turn(Artifact({"main.tf": "bad"}), Feedback(Stage.PLAN, "Runtime", "Error: x"))
    backend.generate(TASK, [turn])
    assert messages.kwargs["system"] == SYSTEM_PROMPT
    assert messages.kwargs["messages"][1]["content"] == '```hcl title="main.tf"\nbad\n```'
    assert messages.kwargs["messages"][2]["content"] == REPAIR_TEMPLATE.format(
        previous_code='```hcl title="main.tf"\nbad\n```',
        stage="PLAN",
        error_class="Runtime",
        message="Error: x",
    )
    assert messages.kwargs["messages"][2]["content"] == render_repair(
        {"main.tf": "bad"}, turn.feedback
    )
    assert backend.params["prompt_version"] == PROMPT_VERSION


def test_custom_prompts_and_version():
    backend, messages = _backend(
        TEXT, system_prompt="SYS", repair_template="ERR:{message}", prompt_version="mod-v1",
        code_lang="bash",
    )  # fmt: skip
    turn = Turn(Artifact({"run.sh": "aws x"}), Feedback(Stage.SANDBOX, "Execution", "boom"))
    reply = backend.generate(TASK, [turn])
    assert messages.kwargs["system"] == "SYS"
    assert messages.kwargs["messages"][1]["content"] == '```bash title="run.sh"\naws x\n```'
    assert messages.kwargs["messages"][2]["content"] == "ERR:boom"
    assert reply.params["prompt_version"] == "mod-v1"


def test_custom_prompt_requires_version():
    import pytest

    with pytest.raises(ValueError, match="prompt_version"):
        _backend(TEXT, system_prompt="SYS")


def test_turn_raw_text_replaces_rendered_artifact():
    turn = Turn(Artifact({}), Feedback(Stage.SANDBOX, "Format", "no block"), raw_text="texto cru")
    msgs = build_messages(TASK, [turn])
    assert msgs[1]["content"] == "texto cru"
