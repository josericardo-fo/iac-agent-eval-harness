"""run_agent_pair com alvo e agente falsos: evento final, guardas e prompt (sem Docker nem LLM)."""

from pathlib import Path

import pytest

from iac_harness.agentic import runner
from iac_harness.agentic.agents import AgentResult
from iac_harness.agentic.runner import AgentRunContext, agent_prompt, run_agent_pair
from iac_harness.modalities.loader import ModalityTask


class FakeExecutor:
    def override_for(self, files):
        return 'provider "aws" {}\n'


class FakeTarget:
    kind = "floci"
    toolchain = None

    def __init__(self):
        self.executor = FakeExecutor()
        self.calls = []

    def reset(self):
        self.calls.append("reset")

    def after_trajectory(self):
        self.calls.append("after")


class FakeAgent:
    name = "fake"

    def __init__(self, **kw):
        self.kw = kw
        self.prompts = []

    def run(self, prompt, workdir, condition, model, target, out_dir):
        self.prompts.append(prompt)
        self.workdir = workdir
        base = dict(
            final_text="done", usage={"input_tokens": 3, "output_tokens": 2}, cost_usd=0.01,
            num_turns=2, tool_calls=[], tool_counts={}, transcript=out_dir / "t.jsonl",
            init={"plugins": []}, exit_code=0, timed_out=False, duration_s=1.0,
            is_error=False, subtype="success",
        )  # fmt: skip
        return AgentResult(**{**base, **self.kw})


class Recorder:
    def __init__(self):
        self.rows = []

    def record(self, row):
        self.rows.append(row)


def _task(category="create", **kw):
    return ModalityTask(
        task_id="t1", category=category, origin="authored", region="us-east-1",
        prompt="Create a bucket.", check="", references={}, **kw,
    )  # fmt: skip


def _plugin_root(tmp_path):
    (tmp_path / "toolkit/plugins/own-tools").mkdir(parents=True)
    (tmp_path / "toolkit/plugins/own-tools/RULES.md").write_text("r")
    return tmp_path


@pytest.fixture(autouse=True)
def no_check(monkeypatch):
    monkeypatch.setattr(runner, "run_check", lambda *a, **k: (True, {}))


def _run(tmp_path, agent, condition="none", task=None, strata=None):
    ctx = AgentRunContext(
        run_id="r", runs_dir=tmp_path / "runs", agent=agent,
        plugin_root=_plugin_root(tmp_path), strata=strata or {},
    )  # fmt: skip
    rec = Recorder()
    final = run_agent_pair(task or _task(), condition, "Haiku 5.5", 1, FakeTarget(), rec, ctx)
    return final, rec


def test_final_records_verify_calls_and_mutation(tmp_path):
    calls = [
        {"name": "Bash", "input": {"command": "terraform apply -auto-approve"}},
        {"name": "Bash", "input": {"command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py"'}},
    ]
    final, _ = _run(tmp_path, FakeAgent(tool_calls=calls))
    assert final["verified_state"] is True
    assert final["mutated"] is True
    assert final["verify_calls"] == 1


def test_tokens_use_metrics_fields(tmp_path):
    final, _ = _run(tmp_path, FakeAgent())
    assert final["input_tokens"] == 3 and final["output_tokens"] == 2
    assert final["cache_read_input_tokens"] == 0 and final["total_tokens"] == 5


def test_strata_fields_cover_every_axis(tmp_path):
    final, _ = _run(tmp_path, FakeAgent(), strata={"t1": {"category": "create", "intent": "clear"}})
    assert final["strata_intent"] == "clear" and final["strata_category"] == "create"
    # eixo ausente na config fica None (mesmas colunas em toda trajetória)
    assert "strata_services" in final and final["strata_services"] is None


def test_init_without_condition_plugin_is_load_fail_even_if_agent_missed_it(tmp_path):
    # segunda guarda: o agente não sinalizou, mas o init não tem o plugin da condição
    final, _ = _run(tmp_path, FakeAgent(init={"plugins": [], "skills": []}), "own-tools")
    assert final["termination"] == "CONDITION_LOAD_FAIL"
    assert final["check_pass"] is None


def test_query_prompt_does_not_mention_harness():
    prompt = agent_prompt(_task("query", initial_tf="x"))
    assert "/work/answer.json" in prompt
    assert "harness" not in prompt.lower()


def test_agent_workdir_override_has_neutral_name(tmp_path):
    agent = FakeAgent()
    _run(tmp_path, agent)
    names = sorted(p.name for p in Path(agent.workdir).glob("*.tf"))
    assert names == ["zz_provider_override.tf"]
