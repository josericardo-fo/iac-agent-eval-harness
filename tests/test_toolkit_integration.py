"""Scripts do own-tools contra o Floci. Exige Docker, a imagem da toolchain e o Floci no ar.

O host de dev não tem boto3, então os scripts rodam na imagem da toolchain (que tem boto3),
na rede `iac-sandbox`. Não reinicia nem derruba o Floci (pode estar em uso): cria recursos com
nomes únicos e apaga só o que criou.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "toolkit" / "plugins" / "own-tools" / "scripts"
IMAGE = "iac-harness-toolchain:latest"
NETWORK = "iac-sandbox"
ENV = {
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ENDPOINT_URL": "http://floci:4566",
    "AWS_EC2_METADATA_DISABLED": "true",
}

pytestmark = pytest.mark.integration


def _floci_up() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "iac-harness-floci-1"],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() == "true"


def _docker(args: list[str], work: Path) -> subprocess.CompletedProcess[str]:
    env_flags = [f for k, v in ENV.items() for f in ("-e", f"{k}={v}")]
    return subprocess.run(
        ["docker", "run", "--rm", "--network", NETWORK, *env_flags,
         "-v", f"{SCRIPTS}:/scripts:ro", "-v", f"{work}:/work", "-w", "/work",
         "--entrypoint", args[0], IMAGE, *args[1:]],
        capture_output=True, text=True, timeout=180,
    )  # fmt: skip


def _script(name: str, *args: str, work: Path) -> tuple[int, dict]:
    proc = _docker(["python3", f"/scripts/{name}.py", *args], work)
    assert proc.stdout, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def _aws(*args: str, work: Path) -> str:
    proc = _docker(["aws", *args], work)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def work(tmp_path):
    if not _floci_up():
        pytest.skip("Floci (iac-harness-floci-1) não está rodando")
    tmp_path.chmod(0o777)  # o container escreve o baseline aqui
    return tmp_path


def test_scripts_against_floci(work):
    suffix = uuid.uuid4().hex[:8]
    bucket, queue, tag = f"tk-it-{suffix}", f"tk-it-{suffix}", f"tk-it-{suffix}"

    code, who = _script("whoami", work=work)
    assert code == 0
    assert who["endpoint"] == "http://floci:4566" and who["region"] == "us-east-1"
    assert who["account"] and who["arn"].startswith("arn:aws")

    code, saved = _script("inventory", "--tag", f"owner={tag}", "--save", "/work/base.json",
                          work=work)  # fmt: skip
    assert code == 0 and saved["counts"] == {}

    queue_url = None
    try:
        _aws("s3api", "create-bucket", "--bucket", bucket, work=work)
        _aws("s3api", "put-bucket-tagging", "--bucket", bucket,
             "--tagging", f"TagSet=[{{Key=owner,Value={tag}}}]", work=work)  # fmt: skip
        queue_url = json.loads(
            _aws("sqs", "create-queue", "--queue-name", queue, "--tags", f"owner={tag}",
                 "--attributes", "MessageRetentionPeriod=345600", work=work)
        )["QueueUrl"]  # fmt: skip

        code, inv = _script("inventory", "--tag", f"owner={tag}", work=work)
        assert code == 0
        found = {(r["type"], r["id"]) for r in inv["resources"]}
        assert found == {("s3.bucket", bucket), ("sqs.queue", queue)}
        assert all(r["tags"] == {"owner": tag} for r in inv["resources"])

        code, ok = _script(
            "verify", "--service", "sqs", "--call", "get-queue-attributes",
            "--params", json.dumps({"QueueUrl": queue_url, "AttributeNames": ["All"]}),
            "--expect", "Attributes.MessageRetentionPeriod=345600", "--timeout", "10",
            work=work,
        )  # fmt: skip
        assert code == 0 and ok["ok"] is True

        code, bad = _script(
            "verify", "--service", "sqs", "--call", "GetQueueAttributes",
            "--params", json.dumps({"QueueUrl": queue_url, "AttributeNames": ["All"]}),
            "--expect", "Attributes.MessageRetentionPeriod=60",
            "--timeout", "2", "--interval", "1", work=work,
        )  # fmt: skip
        assert code == 1 and bad["ok"] is False and bad["attempts"] >= 2

        code, d = _script("diff_baseline", "/work/base.json", work=work)
        assert code == 0
        assert {(r["type"], r["id"]) for r in d["added"]} == found
        assert d["removed"] == []
    finally:
        if queue_url:
            _docker(["aws", "sqs", "delete-queue", "--queue-url", queue_url], work)
        _docker(["aws", "s3api", "delete-bucket", "--bucket", bucket], work)

    code, after = _script("inventory", "--tag", f"owner={tag}", work=work)
    assert code == 0 and after["resources"] == []
