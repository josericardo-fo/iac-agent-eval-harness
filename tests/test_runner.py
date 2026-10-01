from pathlib import Path

from fakes import FakeBackend, FakeExecutor, FakeVerifier
from iac_harness.conditions import A, B, C
from iac_harness.runner import RunContext, SandboxGate, run_task
from iac_harness.types import CheckStatus, Stage, Task, TaskOracle

TASK = Task("aws/task-1", "prompt", 2)
ORACLE = TaskOracle({}, "package terraform.policy")


class ListRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, event: dict) -> None:
        self.events.append(event)


def make(tmp_path: Path, validate_pass=1, sandbox_pass=1, sandbox_status=CheckStatus.FAIL):
    executor = FakeExecutor(sandbox_pass, sandbox_status, "bucket nao suportado")
    gates = {
        Stage.VALIDATE: FakeVerifier(Stage.VALIDATE, validate_pass, message="validate quebrou"),
        Stage.PLAN: FakeVerifier(Stage.PLAN),
        Stage.OPA: FakeVerifier(Stage.OPA),
        Stage.SANDBOX: SandboxGate(executor),
    }
    ctx = RunContext("run1", tmp_path, "rev123")
    return gates, executor, ctx, FakeBackend(), ListRecorder()


def run(tmp_path, conditions, **kw):
    gates, executor, ctx, backend, rec = make(tmp_path, **kw)
    finals = run_task(TASK, ORACLE, 0, conditions, backend, gates, rec, ctx)
    return finals, backend, rec, executor


def test_attempt_one_is_shared(tmp_path):
    finals, backend, rec, _ = run(tmp_path, [A, B, C])
    first = [e for e in rec.events if e["event"] == "attempt" and e["attempt_index"] == 1]
    assert len({e["artifact_hash"] for e in first}) == 1
    assert len({e["request_id"] for e in first}) == 1
    assert backend.calls == 1  # tudo passou: só a geração compartilhada
    assert all(f.final_status == "PASS" for f in finals)


def test_condition_a_single_call_no_repair(tmp_path):
    finals, backend, rec, _ = run(tmp_path, [A], validate_pass=2)
    assert backend.calls == 1
    assert finals[0].attempts == 1
    assert finals[0].final_status == "FAIL"
    assert finals[0].first_failure_stage == "VALIDATE_FAIL"


def test_b_and_c_respect_budget(tmp_path):
    finals, backend, rec, _ = run(tmp_path, [B, C], validate_pass=99)
    assert all(f.attempts == 3 for f in finals)
    assert all(f.termination_reason == "BUDGET_EXHAUSTED" for f in finals)
    assert backend.calls == 1 + 2 + 2  # compartilhada + 2 reparos em B + 2 em C


def test_repair_recovers_and_stops(tmp_path):
    finals, backend, _, _ = run(tmp_path, [B], validate_pass=2)
    assert finals[0].attempts == 2
    assert finals[0].termination_reason == "PASSED"
    assert finals[0].final_status == "PASS"


def test_c_gets_sandbox_feedback_and_b_does_not(tmp_path):
    finals, backend, _, executor = run(tmp_path, [B, C], sandbox_pass=2)
    by = {f.condition: f for f in finals}
    assert by["B"].attempts == 1 and by["B"].final_status == "FAIL"
    assert by["B"].first_failure_stage == "SANDBOX_FAIL"  # medido só ao final
    assert by["C"].attempts == 2 and by["C"].final_status == "PASS"
    # a única chamada com histórico veio de C e traz feedback do sandbox
    with_history = [h for h in backend.histories if h]
    assert len(with_history) == 1
    assert with_history[0][0].feedback.stage is Stage.SANDBOX


def test_unsupported_does_not_trigger_repair(tmp_path):
    finals, backend, rec, _ = run(
        tmp_path, [C], sandbox_pass=99, sandbox_status=CheckStatus.UNSUPPORTED
    )
    assert backend.calls == 1
    assert finals[0].termination_reason == "SANDBOX_UNSUPPORTED"
    assert finals[0].final_status == "SANDBOX_UNSUPPORTED"
    assert all(not h for h in backend.histories)


def test_final_evaluation_identical_across_conditions(tmp_path):
    gates, executor, ctx, backend, rec = make(tmp_path)
    run_task(TASK, ORACLE, 0, [A, B, C], backend, gates, rec, ctx)
    finals = [e for e in rec.events if e["event"] == "final"]
    assert len(finals) == 3
    assert {tuple(sorted(f["statuses"].items())) for f in finals} == {
        (
            ("OPA", "PASS"),
            ("PLAN", "PASS"),
            ("SANDBOX", "PASS"),
            ("VALIDATE", "PASS"),
        )
    }
    # cada condição rodou a cadeia completa: 3 finais; reset antes de cada apply
    assert gates[Stage.PLAN].calls >= 3
    assert executor.resets == executor.applies


def test_attempt_dirs_are_separate_and_never_overwritten(tmp_path):
    _, _, rec, _ = run(tmp_path, [B], validate_pass=3)
    paths = [e["artifact_path"] for e in rec.events if e["event"] == "attempt"]
    assert len(set(paths)) == len(paths) == 3
    assert all((Path(p) / "main.tf").exists() for p in paths)


def test_harness_invalid_flag(tmp_path):
    gates, _, ctx, backend, rec = make(tmp_path)
    finals = run_task(TASK, ORACLE, 0, [A], backend, gates, rec, ctx, harness_valid=False)
    assert finals[0].final_status == "HARNESS_INVALID"


def test_attempt_records_trajectory_fields(tmp_path):
    _, _, rec, _ = run(tmp_path, [B], validate_pass=2)
    first, second = [e for e in rec.events if e["event"] == "attempt"]
    assert (
        first["first_failure_stage"] == "VALIDATE_FAIL" and first["first_failure_status"] == "FAIL"
    )
    assert "validate quebrou" in first["feedback_sent"]
    assert first["diagnostic_chars"] == len("validate quebrou")
    assert "VALIDATE" in first["durations"]
    assert second["feedback_sent"] is None and second["first_failure_stage"] == "PASS"
    assert (Path(first["response_path"])).exists()


def test_shared_tokens_separated_from_repair(tmp_path):
    finals, _, _, _ = run(tmp_path, [A, B], validate_pass=2)
    by = {f.condition: f for f in finals}
    assert by["A"].shared_input_tokens == 10 and by["A"].repair_input_tokens == 0
    assert by["B"].shared_input_tokens == 10 and by["B"].repair_input_tokens == 10


def test_gate_error_is_harness_error_not_agent_fail(tmp_path):
    gates, _, ctx, backend, rec = make(tmp_path)
    gates[Stage.PLAN] = FakeVerifier(Stage.PLAN, 99, CheckStatus.ERROR, "timeout")
    finals = run_task(TASK, ORACLE, 0, [B], backend, gates, rec, ctx)
    assert finals[0].termination_reason == "GATE_ERROR"
    assert finals[0].first_failure_stage == "HARNESS_ERROR"
    assert finals[0].final_status == "HARNESS_ERROR"


def test_unsupported_task_c_behaves_as_b_and_skips_sandbox(tmp_path):
    # sandbox falharia (FAIL) se fosse chamado: nunca pode virar feedback nem rodar na final
    gates, executor, ctx, backend, rec = make(tmp_path, sandbox_pass=99)
    finals = run_task(TASK, ORACLE, 0, [C], backend, gates, rec, ctx, sandbox_supported=False)
    assert executor.applies == 0 and executor.resets == 0
    assert finals[0].condition == "C"
    assert finals[0].attempts == 1
    assert all(not h for h in backend.histories)
    assert finals[0].statuses["SANDBOX"] == "SKIPPED"
    assert finals[0].first_failure_stage == "SANDBOX_UNSUPPORTED"
    assert finals[0].final_status == "SANDBOX_UNSUPPORTED"
    assert finals[0].sandbox_supported is False


def test_unsupported_task_predeploy_failure_is_normal_fail(tmp_path):
    gates, executor, ctx, backend, rec = make(tmp_path, validate_pass=99)
    finals = run_task(TASK, ORACLE, 0, [C], backend, gates, rec, ctx, sandbox_supported=False)
    assert finals[0].final_status == "FAIL"
    assert finals[0].first_failure_stage == "VALIDATE_FAIL"
    assert executor.applies == 0
