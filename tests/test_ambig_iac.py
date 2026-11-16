"""E16: Ambig-IaC como tarefas do agente (linha → tarefa, oráculo plan + OPA, seleção)."""

import json
import subprocess
from pathlib import Path

import pytest

from iac_harness.datasets import ambig_iac
from iac_harness.verifiers import plan_rego

ROW = {
    "id": 7,
    "prompt": "I need somewhere to keep files.  \n",
    "prompt_original": "Create an S3 bucket named sample.",
    "intent": 'Has one "aws_s3_bucket" resource\n    with "bucket"',
    "main_tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "sample"\n}\n',
    "checks_rego": "package terraform.validation\n",
    "plan_json": "{}",
}


def test_row_becomes_create_task_with_prompt_plus_fixed_line():
    task = ambig_iac.row_to_task(ROW)
    assert task.task_id == "ambig-007"
    assert task.category == "create" and task.expect == "act"
    assert task.prompt == (
        "I need somewhere to keep files.\n\n"
        "Write the Terraform configuration for this in main.tf in the working directory.\n"
    )


def test_intent_is_the_hidden_user_card_and_rego_is_the_oracle():
    task = ambig_iac.row_to_task(ROW)
    assert task.user_card == ROW["intent"]
    assert task.rego_policy == ROW["checks_rego"]
    assert task.references == {"terraform": ROW["main_tf"]}
    assert task.missing_references == ("cli", "boto3")
    assert "main.tf" in task.adaptation and task.origin == "ambig-iac:7"


def test_fetch_script_pins_the_same_revision_and_parquet_hash():
    text = (Path(__file__).parents[1] / "scripts" / "fetch_ambig_iac.py").read_text()
    assert f'REVISION = "{ambig_iac.REVISION}"' in text
    assert f'PARQUET_SHA256 = "{ambig_iac.PARQUET_SHA256}"' in text


def test_jsonl_with_other_hash_is_refused(tmp_path):
    ambig_iac.jsonl_path(tmp_path).write_text(json.dumps(ROW) + "\n")
    with pytest.raises(SystemExit, match="sha256"):
        ambig_iac.load_rows(tmp_path)


def test_missing_jsonl_points_to_the_fetch_script(tmp_path):
    with pytest.raises(SystemExit, match="fetch_ambig_iac"):
        ambig_iac.load_rows(tmp_path)


# ---------- oráculo: plan + OPA sobre os .tf finais ----------


class FakeToolchain:
    """Toolchain falsa: responde por comando e registra os arquivos vistos no plan e no opa."""

    target = "emulator"
    endpoint = "http://floci:4566"
    mode = "local"

    def __init__(self, plan_rc=0, plan_err="", opa_value=None, opa_rc=0):
        self.plan_rc, self.plan_err, self.opa_rc = plan_rc, plan_err, opa_rc
        default = {"terraform": {"validation": {"ok": True}}}
        self.opa_value = default if opa_value is None else opa_value
        self.calls, self.seen = [], {}

    def run(self, args, workdir, timeout_s=None):
        self.calls.append(list(args))
        workdir = Path(workdir)
        if args[:2] == ["terraform", "plan"]:
            self.seen["plan"] = sorted(str(p.relative_to(workdir)) for p in workdir.rglob("*"))
            return subprocess.CompletedProcess(args, self.plan_rc, "", self.plan_err)
        if args[:2] == ["terraform", "show"]:
            return subprocess.CompletedProcess(args, 0, '{"resource_changes": []}', "")
        if args[0] == "opa":
            self.seen["policy"] = (workdir / "policy.rego").read_text()
            out = {"result": [{"expressions": [{"value": self.opa_value}]}]}
            return subprocess.CompletedProcess(args, self.opa_rc, json.dumps(out), "boom")
        return subprocess.CompletedProcess(args, 0, "", "")


def _work(tmp_path, files):
    work = tmp_path / "work"
    work.mkdir()
    for name, text in files.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text)
    return work


REGO = "package terraform.validation\n"


def test_no_tf_fails_without_running_anything(tmp_path):
    tc = FakeToolchain()
    work = _work(tmp_path, {"notes.txt": "x", "zz_provider_override.tf": "provider"})
    passed, details = plan_rego.grade_config(tc, REGO, work)
    assert (passed, details["reason"]) == (False, "no_tf") and tc.calls == []


def test_missing_workspace_fails_as_no_tf():
    assert plan_rego.grade_config(FakeToolchain(), REGO, None) == (False, {"reason": "no_tf"})


def test_failed_plan_fails_with_the_error(tmp_path):
    tc = FakeToolchain(plan_rc=1, plan_err="Error: Unsupported argument\n")
    passed, details = plan_rego.grade_config(tc, REGO, _work(tmp_path, {"main.tf": "x"}))
    assert (passed, details["reason"]) == (False, "plan_failed")
    assert "Unsupported argument" in details["error"]
    assert not any(c[0] == "opa" for c in tc.calls)


def test_rego_pass_grades_a_clean_copy_without_state_or_agent_override(tmp_path):
    work = _work(tmp_path, {
        "main.tf": "m", "modules/net/main.tf": "n", "terraform.tfstate": "{}",
        "terraform.tfstate.backup": "{}", ".terraform.lock.hcl": "l",
        "zz_provider_override.tf": "agent", ".terraform/providers/x": "p",
    })  # fmt: skip
    tc = FakeToolchain()
    passed, details = plan_rego.grade_config(tc, REGO, work)
    assert (passed, details["reason"]) == (True, "rego_pass")
    assert tc.seen["plan"] == [
        "main.tf",
        "modules",
        "modules/net",
        "modules/net/main.tf",
        "zz_harness_override.tf",
    ]
    assert tc.seen["policy"] == REGO
    assert (work / "terraform.tfstate").exists()  # o /work do agente não muda


def test_rego_with_a_false_rule_fails_and_names_it(tmp_path):
    value = {"terraform": {"validation": {"is_valid_bucket": True, "is_valid_policy": False,
                                           "names": ["a"]}}}  # fmt: skip
    passed, details = plan_rego.grade_config(
        FakeToolchain(opa_value=value), REGO, _work(tmp_path, {"main.tf": "x"})
    )
    assert (passed, details["reason"]) == (False, "rego_fail")
    assert details["failed_rules"] == ["terraform.validation.is_valid_policy"]


def test_opa_error_fails_as_oracle_error(tmp_path):
    passed, details = plan_rego.grade_config(
        FakeToolchain(opa_rc=1), REGO, _work(tmp_path, {"main.tf": "x"})
    )
    assert (passed, details["reason"]) == (False, "oracle_error")


def test_rego_v0_policies_run_v0_compatible_and_rego_v1_ones_do_not():
    assert "--v0-compatible" in plan_rego.opa_args("package p\nallow { true }\n")
    assert "--v0-compatible" not in plan_rego.opa_args("package p\nimport rego.v1\n")


def test_agent_override_name_matches_the_runner():
    from iac_harness.agentic.runner import AGENT_OVERRIDE_FILENAME

    assert plan_rego.AGENT_OVERRIDE_FILENAME == AGENT_OVERRIDE_FILENAME


def test_run_check_uses_the_rego_oracle_for_dataset_tasks(tmp_path):
    from iac_harness.modalities.execute import run_check

    task = ambig_iac.row_to_task(ROW)
    passed, details = run_check(FakeToolchain(), task, _work(tmp_path, {"main.tf": "x"}))
    assert (passed, details["reason"]) == (True, "rego_pass")


# ---------- sorteio (scripts/select_e16_tasks.py) ----------


def _selector():
    import importlib.util

    path = Path(__file__).parents[1] / "scripts" / "select_e16_tasks.py"
    spec = importlib.util.spec_from_file_location("select_e16_tasks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_draw_order_is_seeded_and_ignores_input_order():
    sel = _selector()
    ids = [ambig_iac.task_id(i) for i in range(300)]
    order = sel.draw_order(ids)
    assert sel.SEED == 20261116
    assert order == sel.draw_order(list(reversed(ids)))
    assert sorted(order) == sorted(ids) and order != sorted(ids)


def test_selection_takes_the_first_passing_tasks_in_draw_order():
    sel = _selector()
    order = ["t5", "t1", "t4", "t2", "t3", "t6"]
    rows = [
        {"task_id": "t1", "gate_pass": False, "reason": "reference: plan_failed"},
        {"task_id": "t5", "gate_pass": True, "reason": ""},
        {"task_id": "t4", "gate_pass": True, "reason": ""},
        {"task_id": "t2", "gate_pass": True, "reason": ""},
    ]
    out = sel.select(order, rows, n=2)
    assert out["selected"] == ["t4", "t5"]  # ordenadas, como no E10
    assert out["substitutes"] == ["t2"]
    assert out["gate_failed"] == [{"task_id": "t1", "reason": "reference: plan_failed"}]
    assert out["not_gated"] == ["t3", "t6"]


# ---------- gate ----------


def test_gate_fails_when_the_reference_does_not_pass(tmp_path):
    tc = FakeToolchain(plan_rc=1, plan_err="Error: bad\n")
    row = ambig_iac.gate_task(tc, ambig_iac.row_to_task(ROW), tmp_path)
    assert (row["gate_pass"], row["reason"]) == (False, "reference: plan_failed")
    assert (tmp_path / "reference" / "main.tf").read_text() == ROW["main_tf"]


def test_gate_fails_when_an_empty_main_tf_also_passes(tmp_path):
    row = ambig_iac.gate_task(FakeToolchain(), ambig_iac.row_to_task(ROW), tmp_path)
    assert (row["gate_pass"], row["reason"]) == (False, "empty main.tf passes")
    assert (tmp_path / "empty" / "main.tf").read_text() == ""


def test_gate_passes_when_reference_passes_and_empty_fails(tmp_path):
    class Tc(FakeToolchain):
        def run(self, args, workdir, timeout_s=None):
            if args[0] == "opa":  # 1ª avaliação (referência) passa, 2ª (vazio) reprova
                self.opa_value = {"ok": not any(c[0] == "opa" for c in self.calls)}
            return super().run(args, workdir, timeout_s)

    row = ambig_iac.gate_task(Tc(), ambig_iac.row_to_task(ROW), tmp_path)
    assert (row["gate_pass"], row["reason"], row["task_id"]) == (True, "", "ambig-007")
    assert row["empty"]["reason"] == "rego_fail"
