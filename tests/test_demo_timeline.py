"""demo-timeline: trajetória gravada -> timeline.json (run dirs sintéticos, sem AWS)."""

import json
import re

from iac_harness.cli import main
from iac_harness.demo.timeline import build_timeline
from test_agentic_metrics import _final

ACCOUNT = "123456789012"


def _tool(name, **inp):
    block = {"type": "tool_use", "name": name, "input": inp}
    return {"type": "assistant", "message": {"content": [block]}}


def _bash(cmd):
    return _tool("Bash", command=cmd)


def _state(resources):
    return {"resources": [
        {"mode": m, "type": t, "name": n, "instances": [{"dependencies": deps}]}
        for m, t, n, deps in resources
    ]}  # fmt: skip


STATE = _state(
    [
        (
            "managed",
            "aws_db_instance",
            "main",
            ["aws_db_subnet_group.main", "aws_security_group.db"],
        ),
        ("managed", "aws_db_subnet_group", "main", ["aws_subnet.a"]),
        ("managed", "aws_subnet", "a", ["aws_vpc.main", "data.aws_availability_zones.az"]),
        ("managed", "aws_security_group", "db", ["aws_vpc.main", "aws_ghost.x"]),
        ("managed", "aws_vpc", "main", []),
        ("data", "aws_availability_zones", "az", []),
    ]
)


def _make(tmp_path, lines, *, state=STATE, judge=None, final=None, mutated=True):
    # judge: bool ou lista de bools (uma linha de veredito por item, na ordem)
    """Cria <run>/<task>/<rep>/<cond>/<slug>/ e devolve o unit_dir."""
    run = tmp_path / "run"
    unit = run / "task-1" / "1" / "own-tools" / "Haiku-5.5"
    (unit / "work").mkdir(parents=True)
    (unit / "transcript.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    if state is not None:
        (unit / "work" / "terraform.tfstate").write_text(json.dumps(state))
    extra = {"mutated": mutated, "final_text": "Pronto.", **(final or {})}
    event = _final("task-1", "own-tools", 1, True, trajectory_id="task-1|own-tools|Haiku 5.5|1",
                   **extra)  # fmt: skip
    (run / "events.jsonl").write_text(json.dumps(event) + "\n")
    if judge is not None:
        verdicts = judge if isinstance(judge, list) else [judge]
        rows = [{"trajectory_id": event["trajectory_id"], "asked": v, "kind": "done"}
                for v in verdicts]  # fmt: skip
        (run / "judge.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return unit


def test_waves_follow_dependencies_and_edges_drop_data(tmp_path):
    out = build_timeline(_make(tmp_path, []))
    waves = {n["id"]: n["wave"] for n in out["nodes"]}
    assert waves["aws_vpc.main"] == 1
    assert waves["aws_db_instance.main"] == max(waves.values()) == 4
    assert {n["id"]: n["group"] for n in out["nodes"]}["aws_db_subnet_group.main"] == "dados"
    assert ["aws_vpc.main", "aws_subnet.a"] in out["edges"]
    assert all("data." not in a + b and "ghost" not in a + b for a, b in out["edges"])
    assert out["scene"] == "task-1" and out["condition"] == "own-tools"


def test_scene_override(tmp_path):
    assert build_timeline(_make(tmp_path, []), scene="cena")["scene"] == "cena"


def test_missing_state_gives_no_nodes(tmp_path):
    out = build_timeline(_make(tmp_path, [], state=None))
    assert out["nodes"] == [] and out["edges"] == []


def test_read_before_apply_and_verify_after(tmp_path):
    lines = [
        _tool("Skill", skill="x"),
        _bash("aws ec2 describe-vpcs"),
        _tool("Write", file_path="/work/main.tf", content="resource {}"),
        _bash("terraform plan -out=p | tail -5"),
        _bash("terraform apply -auto-approve | tail -40"),
        _bash("aws ec2 describe-vpcs"),
    ]
    kinds = [s["kind"] for s in build_timeline(_make(tmp_path, lines))["steps"]]
    assert kinds == ["think", "read", "write", "plan", "apply", "verify", "done"]


def test_mixed_read_and_mutate_bash_is_apply(tmp_path):
    lines = [_bash("aws s3 ls && aws s3 rm s3://b --recursive")]
    kinds = [s["kind"] for s in build_timeline(_make(tmp_path, lines))["steps"]]
    assert kinds == ["apply", "done"]


def test_unclassified_calls_emit_no_step_and_collapse_across_them(tmp_path):
    lines = [
        _bash("python3 /plugin/scripts/whoami.py"),
        _bash("ls"),
        _bash("terraform init"),
        _bash("python3 /plugin/scripts/whoami.py"),
    ]
    steps = build_timeline(_make(tmp_path, lines))["steps"]
    assert [(s["kind"], s.get("script")) for s in steps] == [("read", "whoami"), ("done", None)]


def test_skill_step_has_own_caption_and_only_merges_with_skill(tmp_path):
    lines = [_tool("Skill", skill="a"), _tool("Skill", skill="b"), _bash("aws ec2 describe-vpcs")]
    steps = build_timeline(_make(tmp_path, lines))["steps"]
    assert [(s["kind"], s["caption"]) for s in steps[:-1]] == [
        ("think", "consultou a skill"), ("read", "leu a conta")]  # fmt: skip


def test_last_judge_verdict_wins(tmp_path):
    out = build_timeline(_make(tmp_path, [], judge=[False, True], mutated=False))
    assert out["asked"] is True and out["steps"][-1]["kind"] == "ask"
    out = build_timeline(_make(tmp_path / "b", [], judge=[True, False], mutated=False))
    assert out["asked"] is False and out["steps"][-1]["kind"] == "done"


def test_consecutive_same_kind_collapse_and_script_caption(tmp_path):
    lines = [
        _bash("python3 /plugin/scripts/whoami.py"),
        _bash("python3 /plugin/scripts/whoami.py"),
        _tool("Read", file_path="/work/a.tf"),
        _bash("python3 /plugin/scripts/inventory.py"),
    ]
    steps = build_timeline(_make(tmp_path, lines))["steps"]
    assert [(s["kind"], s.get("script")) for s in steps[:-1]] == [
        ("read", "whoami"), ("read", None), ("read", "inventory")]  # fmt: skip
    assert [s["t"] for s in steps] == list(range(len(steps)))


def test_ask_with_judge_and_question(tmp_path):
    text = "Achei dois buckets.\n\nQual deles devo apagar?"
    out = build_timeline(_make(tmp_path, [_bash("aws s3 ls")], judge=True,
                               final={"final_text": text}, mutated=False))  # fmt: skip
    assert out["asked"] is True and out["steps"][-1]["kind"] == "ask"
    assert out["question"] == "Qual deles devo apagar?"


def test_ask_by_question_mark_without_judge(tmp_path):
    out = build_timeline(_make(tmp_path, [], final={"final_text": "Posso apagar?  "},
                               mutated=False))  # fmt: skip
    assert out["steps"][-1]["kind"] == "ask"


def test_done_when_mutated_even_if_judge_asked_keeps_asked_true(tmp_path):
    out = build_timeline(_make(tmp_path, [], judge=True, final={"final_text": "Feito?"}))
    assert out["steps"][-1]["kind"] == "done" and "question" not in out
    assert out["asked"] is True


def test_account_id_and_secret_never_reach_output(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value-1234")
    lines = [_bash(f"echo sk-secret-value-1234 {ACCOUNT}")]
    final = {"final_text": f"Conta {ACCOUNT} sk-secret-value-1234 ok?"}
    out = tmp_path / "o.json"
    unit = _make(tmp_path, lines, final=final, mutated=False)
    assert main(["demo-timeline", str(unit), "-o", str(out)]) == 0
    text = out.read_text()
    assert "sk-secret-value-1234" not in text and not re.search(r"\b\d{12}\b", text)
    assert "************" in text


def test_cli_prints_to_stdout_without_output_flag(tmp_path, capsys):
    assert main(["demo-timeline", str(_make(tmp_path, []))]) == 0
    assert json.loads(capsys.readouterr().out)["scene"] == "task-1"


def test_cli_missing_dir_is_error(tmp_path):
    import pytest

    with pytest.raises(SystemExit, match="erro"):
        main(["demo-timeline", str(tmp_path / "nope")])
