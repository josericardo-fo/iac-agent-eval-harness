"""TaskSource: registro por `[modality] dataset` e fonte de diretório (padrão)."""

import json
from pathlib import Path

import pytest

from iac_harness.cli.common import _load_cfg_tasks
from iac_harness.datasets import ambig_iac
from iac_harness.datasets.sources import (
    TASK_SOURCES,
    AmbigIacSource,
    DirectorySource,
    task_source,
)
from iac_harness.modalities.loader import TASKS_DIR, load_tasks

ROW = {
    "id": 7,
    "prompt": "I need somewhere to keep files.\n",
    "prompt_original": "Create an S3 bucket named sample.",
    "intent": "one bucket",
    "main_tf": 'resource "aws_s3_bucket" "b" {}\n',
    "checks_rego": "package terraform.validation\n",
    "plan_json": "{}",
}


def test_without_dataset_tasks_come_from_the_default_directory():
    source = task_source({}, Path("data"))
    assert source == DirectorySource(TASKS_DIR)
    assert source.provenance() is None
    assert source.load() == load_tasks() == _load_cfg_tasks({})


def test_tasks_dir_selects_another_directory(tmp_path):
    assert task_source({"tasks_dir": str(tmp_path)}, Path("data")) == DirectorySource(tmp_path)
    assert task_source({"tasks_dir": str(tmp_path)}, Path("data")).load() == []


def test_unknown_dataset_is_refused():
    with pytest.raises(SystemExit, match="dataset desconhecido 'iac-eval'"):
        task_source({"dataset": "iac-eval"}, Path("data"))


def test_ambig_iac_is_registered_with_prompt_field_and_rules_file(tmp_path):
    assert set(TASK_SOURCES) == {ambig_iac.DATASET}
    mod_cfg = {
        "dataset": "ambig-iac", "prompt_field": "prompt_original", "rules_file": "r.json",
        "tasks_dir": "ignored",
    }  # fmt: skip
    source = task_source(mod_cfg, tmp_path)
    assert source == AmbigIacSource(tmp_path, "prompt_original", Path("r.json"))
    assert source.provenance() == ambig_iac.provenance("prompt_original")


def test_ambig_iac_applies_the_discriminating_rules(monkeypatch, tmp_path):
    rows = [ROW, {**ROW, "id": 8}]
    monkeypatch.setattr(ambig_iac, "load_rows", lambda data_dir: rows)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"amendment": {"discriminating_rules": {"ambig-007": ["a.b"]}}}))
    tasks = AmbigIacSource(tmp_path, rules_file=rules).load()
    assert [(t.task_id, t.rego_rules) for t in tasks] == [
        ("ambig-007", ("a.b",)),
        ("ambig-008", None),
    ]
    assert AmbigIacSource(tmp_path).load() == [ambig_iac.row_to_task(r) for r in rows]
