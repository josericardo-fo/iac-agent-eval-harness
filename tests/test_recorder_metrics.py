import pytest

from iac_harness.metrics import (
    compute_metrics,
    holm,
    load_events,
    mcnemar_exact,
    paired_bootstrap,
    pass_at_k,
    wilson,
)
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.types import Artifact


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "sub" / "events.jsonl"
    rec = JsonlRecorder(path)
    rec.record({"event": "x", "v": 1, "s": "ação"})
    # flush imediato: legível antes de fechar
    assert load_events(path) == [{"event": "x", "s": "ação", "v": 1}]
    rec.record({"event": "y"})
    rec.close()
    assert len(load_events(path)) == 2


def test_wilson_known_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) == (0.0, 0.0)


def test_mcnemar_exact_small_table():
    assert mcnemar_exact(1, 5) == pytest.approx(0.21875)
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0


def test_holm():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_bootstrap_deterministic_and_paired():
    a = {"t1": 0.0, "t2": 0.0, "t3": 0.0}
    b = {"t1": 1.0, "t2": 1.0, "t3": 1.0}
    assert paired_bootstrap(a, b, 200, 1) == (1.0, 1.0, 1.0)
    x = {"t1": 0.0, "t2": 1.0, "t3": 0.0, "t4": 1.0}
    y = {"t1": 1.0, "t2": 1.0, "t3": 1.0, "t4": 0.0}
    assert paired_bootstrap(x, y, 200, 7) == paired_bootstrap(x, y, 200, 7)


def test_artifact_hash_is_order_independent():
    a = Artifact({"a.tf": "1", "b.tf": "2"})
    b = Artifact({"b.tf": "2", "a.tf": "1"})
    assert a.sha256 == b.sha256
    assert a.sha256 != Artifact({"a.tf": "12", "b.tf": ""}).sha256


def final(task, cond, rep, status, diff=1, attempts=1, stage="PASS", sup=True, st=None):
    statuses = st or {"VALIDATE": "PASS", "PLAN": "PASS", "OPA": "PASS", "SANDBOX": "PASS"}
    return {
        "event": "final", "task_id": task, "condition": cond, "rep": rep, "difficulty": diff,
        "final_status": status, "attempts": attempts, "first_failure_stage": stage,
        "sandbox_supported": sup, "statuses": statuses, "model_id": "m",
        "shared_input_tokens": 1_000_000, "shared_output_tokens": 500_000,
        "repair_input_tokens": 0, "repair_output_tokens": 0,
    }  # fmt: skip


def test_compute_metrics_small_dataset():
    sbx_fail = {"VALIDATE": "PASS", "PLAN": "PASS", "OPA": "PASS", "SANDBOX": "FAIL"}
    events = [
        final("t1", "A", 0, "FAIL", stage="SANDBOX_FAIL", st=sbx_fail),
        final("t1", "B", 0, "FAIL", attempts=3, stage="SANDBOX_FAIL", st=sbx_fail),
        final("t1", "C", 0, "PASS", attempts=2),
        final("t2", "A", 0, "PASS", diff=2),
        final("t2", "B", 0, "PASS", diff=2),
        final("t2", "C", 0, "PASS", diff=2),
        final("t4", "A", 0, "HARNESS_ERROR", stage="HARNESS_ERROR"),
        final("t3", "A", 0, "HARNESS_INVALID"),
        final("t3", "B", 0, "HARNESS_INVALID"),
        final("t3", "C", 0, "HARNESS_INVALID"),
        {"event": "attempt", "task_id": "t1", "condition": "C", "rep": 0,
         "first_failure_stage": "SANDBOX_FAIL", "first_failure_status": "FAIL"},
        {"event": "attempt", "task_id": "t2", "condition": "C", "rep": 0,
         "first_failure_stage": "SANDBOX_UNSUPPORTED", "first_failure_status": "UNSUPPORTED"},
    ]  # fmt: skip
    m = compute_metrics(events, {"m": (3.0, 15.0)}, n_boot=100)
    assert m["harness_invalid_tasks"] == ["t3"]
    e2e = m["populations"]["end_to_end"]
    assert e2e["baseline_pass_at_1"]["k"] == 1 and e2e["baseline_pass_at_1"]["n"] == 2
    pop = e2e["conditions"]
    assert pop["A"]["success"]["rate"] == 0.5
    assert pop["C"]["success"]["rate"] == 1.0
    assert pop["C"]["attempts_to_success"] == 1.5
    assert pop["A"]["by_difficulty"]["2"]["k"] == 1
    assert e2e["baseline_pass_at_1_by_difficulty"]["2"]["k"] == 1
    # pré-deploy: t1 e t2 passam validate+plan+OPA em todas as condições
    assert m["populations"]["predeploy"]["conditions"]["A"]["success"]["rate"] == 1.0
    c = m["conditions"]
    assert c["A"]["sandbox_only_detection"]["k"] == 1
    assert c["A"]["sandbox_only_detection"]["n"] == 2
    assert c["A"]["cost_usd"] == pytest.approx(2 * (3.0 + 7.5))
    assert m["harness_error"] == {"count": 1, "by_condition": {"A": 1}}
    # tentativa 1 compartilhada conta uma vez por (task, rep) no custo total
    assert m["total_cost_usd"] == pytest.approx(2 * (3.0 + 7.5))
    assert m["sandbox_repair_rate"]["k"] == 1 and m["sandbox_repair_rate"]["n"] == 1
    pair = {p["pair"]: p for p in e2e["comparisons"]}
    assert pair["AxC"]["only_second"] == 1 and pair["AxC"]["only_first"] == 0
    assert pair["AxC"]["mcnemar_p"] == 1.0
    assert "final_success" not in c["A"] and "comparisons" not in m


def test_unsupported_task_in_predeploy_not_in_end_to_end():
    events = [
        final("t1", "A", 0, "PASS"),
        final("t9", "A", 0, "SANDBOX_UNSUPPORTED", stage="SANDBOX_UNSUPPORTED", sup=False,
              st={"VALIDATE": "PASS", "PLAN": "PASS", "OPA": "PASS", "SANDBOX": "SKIPPED"}),
    ]  # fmt: skip
    m = compute_metrics(events, n_boot=10)
    pre, e2e = m["populations"]["predeploy"], m["populations"]["end_to_end"]
    assert pre["n_tasks"] == 2 and e2e["n_tasks"] == 1
    assert pre["conditions"]["A"]["success"]["k"] == 2
    assert e2e["conditions"]["A"]["success"]["n"] == 1


def test_pass_at_k_known_values():
    assert pass_at_k(5, 1, 3) == pytest.approx(0.6)
    assert pass_at_k(5, 0, 3) == 0
    assert pass_at_k(5, 5, 3) == 1
    assert pass_at_k(1, 1, 3) is None


def test_compute_metrics_has_pass_at_k_block():
    events = []
    for task, wins in (("t1", 1), ("t2", 5), ("t3", 0)):
        for rep in range(1, 6):
            status = "PASS" if rep <= wins else "FAIL"
            events.append(final(task, "A", rep, status))
            events.append(final(task, "B", rep, "PASS"))
    pop = compute_metrics(events, n_boot=50)["populations"]["end_to_end"]
    block = pop["baseline_pass_at_k"]
    assert block["k"] == 3 and block["n_tasks"] == 3
    assert block["mean"] == pytest.approx((0.6 + 1.0 + 0.0) / 3)
    assert block["by_difficulty"]["1"] == pytest.approx(block["mean"])
    cmp = {c["pair"]: c for c in pop["pass_at_k_comparisons"]}
    assert set(cmp) == {"pass@3xB"}
    assert cmp["pass@3xB"]["diff_mean"] == pytest.approx(1 - block["mean"])


def test_pass_at_k_is_none_with_single_rep():
    m = compute_metrics([final("t1", "A", 1, "PASS"), final("t1", "B", 1, "PASS")], n_boot=10)
    pop = m["populations"]["end_to_end"]
    assert pop["baseline_pass_at_k"] is None and pop["pass_at_k_comparisons"] is None
