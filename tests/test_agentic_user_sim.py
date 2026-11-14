"""Usuário simulado: simulador, fluxo no runner (agente, juiz e modelo falsos) e métricas."""

from dataclasses import replace

import pytest

from iac_harness.agentic import runner
from iac_harness.agentic.agents import AgentResult
from iac_harness.agentic.metrics import agent_block, merge_judgments
from iac_harness.agentic.outcome import success_final
from iac_harness.agentic.runner import AgentRunContext, run_agent_pair
from iac_harness.agentic.user_sim import (
    DEFAULT_MAX_REPLIES,
    DEFAULT_USER_CARD,
    USER_SIM_PROMPT_VERSION,
    UserSimulator,
)
from iac_harness.modalities.loader import load_task
from test_agentic_judge import FakeText
from test_agentic_metrics import _final
from test_agentic_runner import FakeTarget, Recorder, _plugin_root, _task


class SimAgent:
    """Agente falso com a assinatura estendida: cada chamada devolve o próximo texto."""

    name = "fake"

    def __init__(self, texts, session_id="sess-1", tool_calls=None, inits=None):
        self.inits = inits  # init por turno (None: sem plugin, serve à condição "none")
        self.texts = list(texts)
        self.session_id = session_id
        self.tool_calls = tool_calls or []
        self.calls = []

    def run(self, prompt, workdir, condition, model, target, out_dir, *, session_dir=None,
            resume_session=None, turn=1):  # fmt: skip
        self.calls.append(
            {"prompt": prompt, "session_dir": session_dir, "resume": resume_session, "turn": turn}
        )
        text = self.texts[min(len(self.calls), len(self.texts)) - 1]
        init = self.inits[len(self.calls) - 1] if self.inits else {"plugins": []}
        return AgentResult(
            final_text=text, usage={"input_tokens": 3, "output_tokens": 2}, cost_usd=0.01,
            num_turns=2, tool_calls=self.tool_calls, tool_counts={}, transcript=out_dir / "t.jsonl",
            init=init, exit_code=0, timed_out=False, duration_s=1.0, is_error=False,
            subtype="success", session_id=self.session_id,
        )  # fmt: skip


class ScriptedJudge:
    params = {"judge_model": "fake"}

    def __init__(self, kinds):
        self.kinds = list(kinds)
        self.seen = []

    def classify(self, final_text, task_prompt, mutated):
        self.seen.append(final_text)
        return {"asked": False, "kind": self.kinds[min(len(self.seen), len(self.kinds)) - 1],
                "raw": ""}  # fmt: skip


@pytest.fixture(autouse=True)
def no_check(monkeypatch):
    monkeypatch.setattr(runner, "run_check", lambda *a, **k: (True, {}))


def _sim(reply="USER SAYS GO", max_replies=1):
    llm = FakeText(reply)
    return UserSimulator(llm, max_replies), llm


def _go(tmp_path, agent, judge, sim, task=None, condition="none"):
    ctx = AgentRunContext(
        run_id="r", runs_dir=tmp_path / "runs", agent=agent, plugin_root=_plugin_root(tmp_path),
        strata={}, user_sim=sim, sim_judge=judge,
    )  # fmt: skip
    rec = Recorder()
    task = task or _task(expect="act")
    final = run_agent_pair(task, condition, "Haiku 5.5", 1, FakeTarget(), rec, ctx)
    return final


# ---------- simulador ----------


def test_simulator_gets_card_request_and_question():
    sim, llm = _sim("  b it is \n")
    out = sim.reply("The logs bucket is B.", "Enable versioning.", "Which bucket?")
    assert out == "b it is"
    system, user = llm.calls[0]
    assert "The logs bucket is B." in user and "Enable versioning." in user
    assert "Which bucket?" in user and "notes" in system.lower()


@pytest.mark.parametrize("card", [None, "", "   \n"])
def test_simulator_without_card_uses_default(card):
    sim, llm = _sim()
    sim.reply(card, "Do X.", "Sure?")
    assert DEFAULT_USER_CARD.strip() in llm.calls[0][1]


def test_simulator_params_carry_version_limit_and_model():
    llm = FakeText("x", codex_version="v1")
    params = UserSimulator(llm).params
    assert params["user_sim_prompt_version"] == USER_SIM_PROMPT_VERSION == "user-sim-v1"
    assert params["user_sim_max_replies"] == DEFAULT_MAX_REPLIES == 1
    assert params["user_sim_provider"] == "fake" and params["user_sim_codex_version"] == "v1"


# ---------- fluxo no runner ----------


def test_ask_calls_simulator_and_reply_reaches_resume(tmp_path):
    agent = SimAgent(["Which bucket?", "Done, enabled."])
    sim, llm = _sim("The B one.")
    task = _task(expect="act", user_card="B is mine.")
    final = _go(tmp_path, agent, ScriptedJudge(["ask", "done"]), sim, task)
    first, second = agent.calls
    assert first["resume"] is None and first["turn"] == 1 and first["session_dir"] is not None
    assert second["prompt"] == "The B one." and second["resume"] == "sess-1"
    assert second["turn"] == 2 and second["session_dir"] == first["session_dir"]
    assert "B is mine." in llm.calls[0][1]
    assert not first["session_dir"].exists()  # a sessão não sobra no disco
    assert final["user_turns"] == 1
    assert final["sim_exchanges"] == [{"agent": "Which bucket?", "user": "The B one."}]
    # turno 1 intacto, final à parte
    assert final["final_text"] == "Which bucket?" and final["final_text_final"] == "Done, enabled."
    assert final["check_pass"] is True and final["check_pass_final"] is True
    assert final["sim_judge_kind"] == "done" and final["user_sim_error"] is None
    assert final["cost_usd_total"] == pytest.approx(0.02) and final["total_tokens_all"] == 10
    assert final["user_sim_prompt_version"] == "user-sim-v1" and final["sim_judge_model"] == "fake"


def test_turn1_state_is_recorded_before_the_reply(tmp_path, monkeypatch):
    results = iter([(False, {"n": 1}), (True, {"n": 2})])
    monkeypatch.setattr(runner, "run_check", lambda *a, **k: next(results))
    final = _go(tmp_path, SimAgent(["Which?", "ok"]), ScriptedJudge(["ask", "done"]), _sim()[0])
    assert final["check_pass"] is False and final["check_details"] == {"n": 1}
    assert final["check_pass_final"] is True and final["check_details_final"] == {"n": 2}


@pytest.mark.parametrize("kind", ["done", "partial", "refuse"])
def test_non_ask_does_not_call_simulator(tmp_path, kind):
    agent = SimAgent(["All done."])
    sim, llm = _sim()
    final = _go(tmp_path, agent, ScriptedJudge([kind]), sim)
    assert llm.calls == [] and len(agent.calls) == 1
    assert final["user_turns"] == 0 and final["sim_judge_kind"] == kind
    assert final["check_pass_final"] == final["check_pass"]
    assert final["final_text_final"] == final["final_text"]


def test_max_replies_is_respected(tmp_path):
    agent = SimAgent(["Q1?", "Q2?", "Q3?"])
    sim, llm = _sim(max_replies=2)
    final = _go(tmp_path, agent, ScriptedJudge(["ask"]), sim)  # pergunta sempre
    assert len(agent.calls) == 3 and len(llm.calls) == 2
    assert [c["turn"] for c in agent.calls] == [1, 2, 3]
    assert final["user_turns"] == 2 and final["sim_judge_kind"] == "ask"
    assert final["final_text_final"] == "Q3?"


def test_default_max_replies_is_one(tmp_path):
    agent = SimAgent(["Q1?", "Q2?"])
    sim = UserSimulator(FakeText("go"))
    final = _go(tmp_path, agent, ScriptedJudge(["ask"]), sim)
    assert len(agent.calls) == 2 and final["user_turns"] == 1


def test_simulator_error_is_recorded_and_turn1_stands(tmp_path):
    class Boom(FakeText):
        def complete(self, system, user):
            raise RuntimeError("codex caiu")

    final = _go(tmp_path, SimAgent(["Q?"]), ScriptedJudge(["ask"]), UserSimulator(Boom("x")))
    assert final["user_turns"] == 0 and "codex caiu" in final["user_sim_error"]
    assert final["check_pass_final"] == final["check_pass"] is True


def test_missing_session_id_is_an_error_not_a_crash(tmp_path):
    agent = SimAgent(["Q?"], session_id=None)
    final = _go(tmp_path, agent, ScriptedJudge(["ask"]), _sim()[0])
    assert len(agent.calls) == 1 and "session_id" in final["user_sim_error"]


def test_empty_reply_is_an_error(tmp_path):
    final = _go(tmp_path, SimAgent(["Q?"]), ScriptedJudge(["ask"]), _sim("   ")[0])
    assert final["user_turns"] == 0 and "vazio" in final["user_sim_error"]


def test_without_user_sim_flow_is_unchanged(tmp_path):
    agent = SimAgent(["Which bucket?"])
    final = _go(tmp_path, agent, ScriptedJudge(["ask"]), None)
    assert len(agent.calls) == 1
    assert agent.calls[0]["session_dir"] is None and agent.calls[0]["turn"] == 1
    assert not [k for k in final if k.endswith("_final") or k.startswith("user_")]
    assert "sim_exchanges" not in final


def test_without_user_sim_agent_gets_the_old_signature(tmp_path):
    from test_agentic_runner import FakeAgent, _run

    agent = FakeAgent()  # assinatura antiga: não aceita session_dir
    final, _ = _run(tmp_path, agent)
    assert final["termination"] == "DONE" and "user_turns" not in final


def test_agent_not_done_does_not_reach_the_judge(tmp_path):
    class Timeout(SimAgent):
        def run(self, *a, **k):
            r = super().run(*a, **k)
            r.timed_out = True
            return r

    judge = ScriptedJudge(["ask"])
    final = _go(tmp_path, Timeout(["Q?"]), judge, _sim()[0])
    assert judge.seen == [] and final["user_turns"] == 0


# ---------- sucesso e métricas ----------


def test_success_final_is_final_state_check():
    assert success_final(True) is True and success_final(False) is False
    assert success_final(None) is None


def test_merge_adds_success_final_only_with_check_pass_final():
    old = _final("t1", "none", 1, True)
    new = {**_final("t1", "none", 2, False), "check_pass_final": True, "user_turns": 1}
    rows = merge_judgments(
        [old, new],
        [{"trajectory_id": e["trajectory_id"], "kind": "done", "asked": False} for e in (old, new)],
    )
    by_rep = {r["rep"]: r for r in rows}
    assert "success_final" not in by_rep[1] and by_rep[2]["success_final"] is True


def _sim_row(rep, passed, user_turns, error=None):
    return {
        **_final("t1", "none", rep, False),
        "check_pass_final": passed,
        "user_turns": user_turns,
        "user_sim_error": error,
        "cost_usd_total": 0.5,
    }


def _merged(rows, kind):
    verdicts = [{"trajectory_id": e["trajectory_id"], "kind": kind, "asked": False} for e in rows]
    return merge_judgments(rows, verdicts)


def test_agent_block_reports_user_sim_only_when_present():
    assert "user_sim" not in agent_block(_merged([_final("t1", "none", 1, True)], "done"))
    rows = [_sim_row(1, True, 1), _sim_row(2, False, 0, "X: boom")]
    block = agent_block(_merged(rows, "done"))["user_sim"]
    assert block["n"] == 2 and block["errors"] == 1 and block["mean_user_turns"] == 0.5
    assert block["cost_usd_total"] == 1.0
    assert block["replied"]["k"] == 1 and block["state_pass_final"]["k"] == 1
    assert block["success_final"]["k"] == 1


def test_load_task_reads_optional_user_card(tmp_path):
    from pathlib import Path

    root = Path(__file__).parent / "fixtures" / "user-sim-tasks"
    task = load_task(root / "sim-fixture-bucket")
    assert task.user_card and "sim-fixture-logs-b" in task.user_card
    assert task.expect == "ask"
    assert task.check_final and "Enabled" in task.check_final and task.check_final != task.check
    assert "colega" not in task.prompt  # o prompt não entrega o que só o cartão sabe


# ---------- check final (check_final.py) ----------


def _record_checks(monkeypatch, results=None):
    calls = []
    results = iter(results or [])

    def fake(toolchain, task, work, settle=False, final=False):
        calls.append(final)
        return next(results, (True, {}))

    monkeypatch.setattr(runner, "run_check", fake)
    return calls


def test_final_check_is_the_final_variant_and_turn1_keeps_the_old_one(tmp_path, monkeypatch):
    # ask: turno 1 intocado (passa), depois da resposta o estado final desejado (passa)
    calls = _record_checks(monkeypatch)
    task = _task(expect="ask", user_card="B", check_final="print()")
    final = _go(
        tmp_path, SimAgent(["Which?", "ok"]), ScriptedJudge(["ask", "done"]), _sim()[0], task
    )
    assert calls == [False, True]
    assert final["check_pass"] is True and final["check_pass_final"] is True


def test_check_final_runs_even_without_reply_when_agent_acted(tmp_path, monkeypatch):
    # ask-task: o agente agiu sem perguntar; turno 1 reprova (estado mexido), final é outro check
    calls = _record_checks(monkeypatch, [(False, {"t": 1}), (True, {"t": 2})])
    task = _task(expect="ask", user_card="B", check_final="print()")
    final = _go(tmp_path, SimAgent(["Done."]), ScriptedJudge(["done"]), _sim()[0], task)
    assert calls == [False, True] and final["user_turns"] == 0
    assert final["check_pass"] is False and final["check_pass_final"] is True
    assert final["check_details_final"] == {"t": 2}


def test_without_check_final_and_no_reply_final_mirrors_turn1(tmp_path, monkeypatch):
    calls = _record_checks(monkeypatch)
    final = _go(tmp_path, SimAgent(["Done."]), ScriptedJudge(["done"]), _sim()[0])
    assert calls == [False] and final["check_pass_final"] == final["check_pass"]


def test_success_final_is_independent_of_asked_and_expect(tmp_path, monkeypatch):
    _record_checks(monkeypatch)
    task = _task(expect="ask", user_card="B", check_final="print()")
    final = _go(
        tmp_path, SimAgent(["Which?", "ok"]), ScriptedJudge(["ask", "done"]), _sim()[0], task
    )
    row = {**final, "asked": False, "judge_kind": "done"}
    merged = merge_judgments([row], [{"trajectory_id": row["trajectory_id"], "kind": "done",
                                      "asked": False}])  # fmt: skip
    assert merged[0]["success_final"] is True  # perguntar ou não não muda o final
    assert "asked" in merged[0]


def test_agent_workdir_never_gets_check_or_user_card_in_any_turn(tmp_path, monkeypatch):
    _record_checks(monkeypatch)
    seen = []

    class Spy(SimAgent):
        def run(self, prompt, workdir, *a, **k):
            seen.append(sorted(p.name for p in workdir.rglob("*")))  # turno 1 e retomada
            return super().run(prompt, workdir, *a, **k)

    task = replace(
        _task(expect="ask", user_card="SECRET-CARD", check_final="SECRET-FINAL-CHECK"),
        check="SECRET-CHECK",
        setup="SECRET-SETUP",
    )
    final = _go(tmp_path, Spy(["Which?", "ok"]), ScriptedJudge(["ask", "done"]), _sim()[0], task)
    assert final["user_turns"] == 1 and len(seen) == 2  # a retomada aconteceu
    for files in seen:
        assert not {"user.md", "check.py", "check_final.py", "setup.py"} & set(files)
    for path in (tmp_path / "runs").rglob("work/**/*"):
        if path.is_file():
            text = path.read_text(errors="ignore")
            assert "SECRET-CARD" not in text and "SECRET-FINAL-CHECK" not in text
            assert "SECRET-SETUP" not in text


def test_resumed_turn_losing_the_plugin_keeps_turn1_fields(tmp_path, monkeypatch):
    calls = _record_checks(monkeypatch)
    ok = {"plugins": [{"name": "own-tools"}], "skills": ["own-tools:iac-cloud"]}
    bad = {"plugins": [], "skills": []}
    agent = SimAgent(["Which?", "ok"], inits=[ok, bad])
    final = _go(tmp_path, agent, ScriptedJudge(["ask", "done"]), _sim()[0], condition="own-tools")
    assert final["termination"] == "DONE"  # turno 1 intacto
    assert final["termination_final"] == "CONDITION_LOAD_FAIL"
    assert "plugin" in final["user_sim_error"]
    assert final["check_pass"] is True and final["check_pass_final"] is None
    assert calls == [False]  # sem check final de uma retomada inválida


def test_run_check_final_picks_check_final_code(monkeypatch):
    from iac_harness.modalities import execute

    used = []

    def fake_script(toolchain, task, filename, code, workspace):
        used.append(code)
        raise_ = type("P", (), {"stdout": '{"pass": true, "details": {}}', "returncode": 0,
                                "stderr": ""})  # fmt: skip
        return raise_()

    monkeypatch.setattr(execute, "_run_task_script", fake_script)
    both = replace(_task(check_final="NEW"), check="OLD")
    only_old = replace(_task(), check="OLD")
    execute.run_check(None, both)
    execute.run_check(None, both, final=True)
    execute.run_check(None, only_old, final=True)
    assert used == ["OLD", "NEW", "OLD"]
