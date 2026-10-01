from pathlib import Path

from iac_harness.datasets.iac_eval_v2 import DEFAULT_REVISION, IacEvalV2Adapter

FIXTURE = Path(__file__).parent / "fixtures" / "iac_eval_v2_sample.csv"


def _adapter(tmp_path: Path) -> IacEvalV2Adapter:
    adapter = IacEvalV2Adapter(tmp_path)
    adapter.csv_path.write_bytes(FIXTURE.read_bytes())  # cache preenchido: sem rede
    return adapter


def test_load_builds_task_and_oracle(tmp_path):
    adapter = _adapter(tmp_path)
    pairs = adapter.load()
    assert adapter.version == DEFAULT_REVISION
    by_id = {t.task_id: (t, o) for t, o in pairs}
    task, oracle = by_id["aws/task-269"]
    assert task.difficulty == 1
    assert "## Intent" in task.prompt
    assert task.metadata["has_starting_state"] == "False"
    assert "intent" in task.metadata
    assert set(oracle.reference_files) == {"main.tf"}
    assert 'resource "aws_s3_bucket"' in oracle.reference_files["main.tf"]
    assert "package terraform.policy" in oracle.rego_policy
    assert task.metadata["opa_input_path"] == "resource_changes"


def test_task_does_not_expose_oracle(tmp_path):
    task, _ = _adapter(tmp_path).load()[0]
    assert "rego_policy" not in task.metadata and "reference_output" not in task.metadata
