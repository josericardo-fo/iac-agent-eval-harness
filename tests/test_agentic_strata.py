import tomllib
from pathlib import Path

import pytest

from iac_harness.agentic.strata import load_strata
from iac_harness.modalities.loader import TASKS_DIR, load_tasks

ROOT = Path(__file__).parent.parent
STRATA = ROOT / "configs" / "strata.toml"
AGENT_CONFIGS = ("e3-agent.toml", "e3-agent-aws.toml", "e3-pilot.toml", "e3-pilot-aws.toml")


def test_every_task_dir_has_strata():
    strata = load_strata(STRATA)
    dirs = {p.name for p in TASKS_DIR.iterdir() if (p / "meta.toml").is_file()}
    assert dirs == set(strata)


def test_strata_category_matches_meta():
    strata = load_strata(STRATA)
    for task in load_tasks():
        assert strata[task.task_id]["category"] == task.category, task.task_id


@pytest.mark.parametrize("name", AGENT_CONFIGS)
def test_every_task_in_agent_config_has_strata(name):
    strata = load_strata(STRATA)
    cfg = tomllib.loads((ROOT / "configs" / name).read_text())
    tasks = cfg["modality"]["tasks"]
    assert tasks and len(tasks) == len(set(tasks))
    assert set(tasks) <= set(strata)


def test_floci_config_has_no_real_only_task():
    strata = load_strata(STRATA)
    cfg = tomllib.loads((ROOT / "configs" / "e3-agent.toml").read_text())
    assert all(strata[t]["target"] == "both" for t in cfg["modality"]["tasks"])


def test_known_intents():
    strata = load_strata(STRATA)
    assert strata["amb-del-s3"]["intent"] == "ambiguous"
    assert strata["amb-s3-versioning"]["intent"] == "false-premise"
    assert strata["task-416"]["intent"] == "cost-lock"
    assert strata["mod-sqs-retention"]["intent"] == "clear"
    assert strata["task-201"]["difficulty"] == 6


def test_invalid_strata_value_is_rejected(tmp_path):
    bad = tmp_path / "s.toml"
    bad.write_text(
        '[tasks.x]\ncategory = "create"\norigin = "authored"\nintent = "weird"\n'
        'target = "both"\nservices = ["S3"]\n'
    )
    with pytest.raises(ValueError):
        load_strata(bad)
