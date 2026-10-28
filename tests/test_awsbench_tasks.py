"""Tarefas adaptadas do aws-bench (tasks/awsb-*): cópias literais, prompt derivado e adaptador.

Garante que upstream/ é idêntico ao commit fixado (hash no meta.toml), que prompt.md é a
transformação determinística de upstream/instruction.md e que o check.py adaptador roda os
critérios do check original.
"""

import ast
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from iac_harness.modalities.loader import TASKS_DIR, load_task

PREFIX = "awsb-"
UPSTREAM_COMMIT = "73f814af0f3ba381e1a797801333ad4478b78971"
OUTPUT_PARAGRAPH = "\n\nIMPORTANT: Write your final prose answer to `/logs/agent/agent-output.txt`."
JSON_INTRO = "Additionally, write `/logs/agent/agent-output.json` containing exactly:"
ANSWER_INTRO = "Answer with this JSON object:"
TASK_DIRS = sorted(p for p in TASKS_DIR.iterdir() if p.name.startswith(PREFIX))


def transform(instruction: str, placeholders: dict[str, str]) -> str:
    """instruction.md → prompt.md: troca os {{...}}, corta o parágrafo de saída em arquivo e
    troca a introdução do JSON em /logs/agent por "Answer with this JSON object:"."""
    text = re.sub(r"\{\{([^}]+)\}\}", lambda m: placeholders[m.group(1)], instruction)
    return text.replace(OUTPUT_PARAGRAPH, "", 1).replace(JSON_INTRO, ANSWER_INTRO, 1)


def _meta(task_dir: Path) -> dict:
    return tomllib.loads((task_dir / "meta.toml").read_text())


def test_awsbench_tasks_present():
    assert {p.name for p in TASK_DIRS} == {
        "awsb-create-s3-bucket-with-config",
        "awsb-move-s3-contents-within-bucket",
        "awsb-sync-s3-buckets-with-metadata",
        "awsb-dynamodb-strip-ec2-fields-from-prod",
        "awsb-lambda-versioned-alias-update",
    }


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=lambda p: p.name)
def test_upstream_files_match_pinned_hashes(task_dir):
    meta = _meta(task_dir)
    assert meta["upstream_commit"] == UPSTREAM_COMMIT
    assert UPSTREAM_COMMIT in meta["upstream_source"]
    assert meta["origin"] == "aws-bench:compute-and-data/" + task_dir.name.removeprefix(PREFIX)
    files = {p.name: p.read_bytes() for p in (task_dir / "upstream").iterdir()}
    assert set(files) == {"check.py", "instruction.md"} == set(meta["upstream_sha256"])
    for name, body in files.items():
        assert hashlib.sha256(body).hexdigest() == meta["upstream_sha256"][name]


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=lambda p: p.name)
def test_prompt_is_transformed_instruction(task_dir):
    meta = _meta(task_dir)
    instruction = (task_dir / "upstream" / "instruction.md").read_text()
    used = set(re.findall(r"\{\{([^}]+)\}\}", instruction))
    assert used == set(meta["placeholders"])
    prompt = (task_dir / "prompt.md").read_text()
    assert prompt == transform(instruction, meta["placeholders"])
    assert "/logs/" not in prompt and "{{" not in prompt
    # answer = true exatamente nas tarefas cujo original pedia o agent-output.json
    assert meta.get("answer", False) == (JSON_INTRO in instruction)


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=lambda p: p.name)
def test_awsbench_task_loads(task_dir):
    meta = _meta(task_dir)
    task = load_task(task_dir)
    assert set(task.upstream) == {"check.py", "instruction.md"}
    assert task.hide_initial == (task.category != "create")
    # o check.py adaptador define exatamente o [check_env] do meta.toml
    env = next(
        n.value for n in ast.parse(task.check).body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ENV"
    )  # fmt: skip
    assert ast.literal_eval(env) == meta["check_env"]


# ---------- adaptador (check.py comum às tarefas awsb-*) ----------

FAKE_UPSTREAM = """
import json
import os
from pathlib import Path

from rewardkit import criterion

OUT = Path("/logs/agent/agent-output.json")
DATA = json.loads(OUT.read_text()) if OUT.exists() else {}


@criterion(description="env set")
def env_set(workspace: Path) -> bool:
    return os.environ["AWS_REGION"] == "us-east-1" and workspace == Path(".")


@criterion(description="answer read")
def answer_read(workspace: Path) -> bool:
    return DATA.get("S3BucketName") == "b"


@criterion(description="boom")
def boom(workspace: Path) -> bool:
    raise RuntimeError("x")
"""


def _run_wrapper(tmp_path: Path, upstream: str, answer: dict | None = None) -> dict:
    wrapper = (TASKS_DIR / "awsb-create-s3-bucket-with-config" / "check.py").read_text()
    (tmp_path / "check.py").write_text(wrapper)
    (tmp_path / "upstream").mkdir()
    (tmp_path / "upstream" / "check.py").write_text(upstream)
    if answer is not None:
        (tmp_path / "answer.json").write_text(json.dumps(answer))
    proc = subprocess.run(
        [sys.executable, "check.py", "us-east-1"], cwd=tmp_path, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_wrapper_reports_each_criterion(tmp_path):
    out = _run_wrapper(tmp_path, FAKE_UPSTREAM)
    assert out["pass"] is False
    assert out["details"] == {
        "env set": True,
        "answer read": False,
        "boom": "RuntimeError: x",
    }


def test_wrapper_reads_answer_json_instead_of_logs_path(tmp_path):
    upstream = FAKE_UPSTREAM.split('@criterion(description="boom")')[0]
    out = _run_wrapper(tmp_path, upstream, answer={"S3BucketName": "b"})
    assert out == {"pass": True, "details": {"env set": True, "answer read": True}}


def test_wrapper_never_crashes(tmp_path):
    out = _run_wrapper(tmp_path, "raise SystemError('no boto3')\n")
    assert out["pass"] is False and "SystemError" in out["details"]["error"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _run_wrapper(empty, "x = 1\n") == {"pass": False, "details": {}}
