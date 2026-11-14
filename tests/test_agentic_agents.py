"""ClaudeCodeAgent.run com o processo do docker falso: guarda do system/init, sem Docker."""

import json

import pytest

from iac_harness.agentic import agents
from iac_harness.agentic.agents import ClaudeCodeAgent
from iac_harness.agentic.conditions import resolve_condition
from iac_harness.agentic.targets import AgentEnv


class FakeTarget:
    kind = "floci"

    def __init__(self):
        self.env_calls = 0

    def agent_env(self):
        self.env_calls += 1
        return AgentEnv({"AWS_REGION": "us-east-1"}, {"AWS_SECRET_ACCESS_KEY": "s3cr3t-value-01"})

    def agent_networks(self):
        return ["iac-sandbox"]


class FakeProc:
    def __init__(self, lines):
        self.stdout = iter(line + "\n" for line in lines)
        self.killed = False

    def wait(self):
        return 0

    def poll(self):
        return 0


INIT_OK = {"type": "system", "subtype": "init", "plugins": [{"name": "own-tools"}],
           "skills": ["own-tools:iac-cloud"]}  # fmt: skip
INIT_BAD = {"type": "system", "subtype": "init", "plugins": [], "skills": []}
HOOK = {"type": "system", "subtype": "hook_started", "hook_name": "SessionStart"}
PLUGIN_INSTALL = {"type": "system", "subtype": "plugin_install", "name": "x"}
ASSISTANT = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "aws s3 ls"}}]}}  # fmt: skip
RESULT = {"type": "result", "subtype": "success", "result": "done", "is_error": False,
          "num_turns": 2, "usage": {}}  # fmt: skip


@pytest.fixture
def fake_docker(monkeypatch):
    calls = {"kill": 0, "popen": 0}

    def install(events):
        def popen(argv, **kw):
            calls["popen"] += 1
            return FakeProc([json.dumps(e) for e in events])

        def run(args, **kw):
            if args[:3] == ["docker", "rm", "-f"]:
                calls["kill"] += 1

        monkeypatch.setattr(agents.subprocess, "Popen", popen)
        monkeypatch.setattr(agents.subprocess, "run", run)
        return calls

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw.example.org")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-gateway-SECRET-0001")
    return install


def _plugin_root(tmp_path):
    (tmp_path / "toolkit/plugins/own-tools").mkdir(parents=True)
    (tmp_path / "toolkit/plugins/own-tools/RULES.md").write_text("r")
    return tmp_path


def _run(tmp_path, events, fake_docker, condition="own-tools"):
    calls = fake_docker(events)
    target = FakeTarget()
    cond = resolve_condition(condition, _plugin_root(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    result = ClaudeCodeAgent().run("p", work, cond, "Haiku 5.5", target, tmp_path / "out")
    return result, calls, target


def test_init_after_hook_events_is_accepted(tmp_path, fake_docker):
    events = [HOOK, PLUGIN_INSTALL, HOOK, HOOK, INIT_OK, ASSISTANT, RESULT]
    result, calls, _ = _run(tmp_path, events, fake_docker)
    assert result.condition_load_fail is False
    assert result.init["plugins"] == [{"name": "own-tools"}]
    assert calls["kill"] == 0 and result.final_text == "done"


def test_init_without_plugin_after_hooks_aborts(tmp_path, fake_docker):
    events = [HOOK, PLUGIN_INSTALL, HOOK, HOOK, INIT_BAD, ASSISTANT, RESULT]
    result, calls, _ = _run(tmp_path, events, fake_docker)
    assert result.condition_load_fail is True
    assert calls["kill"] == 1
    assert result.tool_calls == []  # nada depois do init foi lido


def test_assistant_before_init_aborts(tmp_path, fake_docker):
    result, calls, _ = _run(tmp_path, [HOOK, ASSISTANT, INIT_OK, RESULT], fake_docker)
    assert result.condition_load_fail is True and calls["kill"] == 1


def test_run_ending_without_init_is_load_fail(tmp_path, fake_docker):
    result, _, _ = _run(tmp_path, [HOOK, RESULT], fake_docker)
    assert result.condition_load_fail is True


def test_none_condition_needs_no_init(tmp_path, fake_docker):
    result, calls, _ = _run(tmp_path, [ASSISTANT, RESULT], fake_docker, condition="none")
    assert result.condition_load_fail is False and calls["kill"] == 0


def test_agent_env_is_built_once_per_run(tmp_path, fake_docker):
    _, _, target = _run(tmp_path, [INIT_OK, RESULT], fake_docker)
    assert target.env_calls == 1


def test_transcript_redacts_target_secret(tmp_path, fake_docker):
    leak = {**RESULT, "result": "key is s3cr3t-value-01"}
    result, _, _ = _run(tmp_path, [INIT_OK, leak], fake_docker)
    assert "s3cr3t-value-01" not in result.transcript.read_text()


# ---------- retomada de sessão (usuário simulado) ----------


def _argv(tmp_path, **kw):
    cond = resolve_condition("none", tmp_path)
    return ClaudeCodeAgent().docker_argv(
        "the prompt", tmp_path, cond, "Haiku 5.5", FakeTarget(), "c1", **kw
    )[0]


def test_docker_argv_without_session_is_the_old_one(tmp_path, fake_docker):
    fake_docker([])
    argv = _argv(tmp_path)
    assert "--resume" not in argv and not any("/home/agent/.claude" in a for a in argv)


def test_docker_argv_with_session_mounts_it_and_resumes(tmp_path, fake_docker):
    fake_docker([])
    plain = _argv(tmp_path)
    argv = _argv(tmp_path, session_dir=tmp_path / "sess", resume_session="abc-123")
    assert argv[argv.index("--resume") + 1] == "abc-123"
    assert f"{tmp_path / 'sess'}:/home/agent/.claude" in argv
    # mesmos flags, condição e modelo: só o mount e o --resume a mais
    k = argv.index(f"{tmp_path / 'sess'}:/home/agent/.claude")
    rest = argv[: k - 1] + argv[k + 1 :]
    r = rest.index("--resume")
    assert rest[:r] + rest[r + 2 :] == plain


def test_session_id_comes_from_init_and_turn_files_are_separate(tmp_path, fake_docker):
    init = {**INIT_OK, "session_id": "sess-xyz"}
    calls = fake_docker([init, ASSISTANT, RESULT])
    cond = resolve_condition("own-tools", _plugin_root(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    agent, target, out = ClaudeCodeAgent(), FakeTarget(), tmp_path / "out"
    first = agent.run("p", work, cond, "Haiku 5.5", target, out, session_dir=tmp_path / "s")
    second = agent.run("r", work, cond, "Haiku 5.5", target, out, session_dir=tmp_path / "s",
                       resume_session="sess-xyz", turn=2)  # fmt: skip
    assert first.session_id == "sess-xyz" and calls["popen"] == 2
    assert first.transcript.name == "transcript.jsonl"
    assert second.transcript.name == "transcript.turn2.jsonl"
    assert (out / "agent.turn2.stderr.txt").exists()


def test_turn_filename():
    from iac_harness.agentic.agents import turn_filename

    assert turn_filename("transcript.jsonl", 1) == "transcript.jsonl"
    assert turn_filename("transcript.jsonl", 3) == "transcript.turn3.jsonl"
    assert turn_filename("agent.stderr.txt", 2) == "agent.turn2.stderr.txt"
