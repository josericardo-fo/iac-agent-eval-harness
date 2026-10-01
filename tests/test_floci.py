import subprocess
from pathlib import Path

import httpx2
import pytest

from iac_harness.executors.floci import DEFAULT_VPC_REGIONS, FlociExecutor
from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckStatus
from iac_harness.workspace import Workspace


class FakeToolchain(Toolchain):
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        super().__init__(mode="local")
        self.returncode, self.stderr, self.calls = returncode, stderr, []

    def run(self, args, workdir, timeout_s=None):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, self.returncode, "", self.stderr)


def _executor(tc=None) -> FlociExecutor:
    return FlociExecutor(tc or FakeToolchain())


def test_override_has_fake_credentials_and_endpoints():
    text = _executor().override_tf()
    assert 'access_key                  = "test"' in text
    assert "skip_credentials_validation = true" in text
    assert "skip_requesting_account_id  = true" in text
    assert "skip_metadata_api_check     = true" in text
    assert "s3_use_path_style           = true" in text
    assert 's3 = "http://floci:4566"' in text and 'kafka = "http://floci:4566"' in text
    assert "alias" not in text
    assert "region" not in text  # a região do artefato não pode ser sobrescrita


def test_override_generates_block_per_alias():
    files = {
        "main.tf": 'provider "aws" {\n  region = "us-west-2"\n}\n'
        'provider "aws" {\n  alias  = "east"\n  region = "us-east-1"\n}\n'
        'resource "aws_s3_bucket" "b" {\n  provider = aws.east\n}\n'
    }
    ex = _executor()
    assert ex.find_aliases(files) == ("east",)
    text = ex.override_for(files)
    assert text.count('provider "aws"') == 2
    assert 'alias                       = "east"' in text


def test_alias_found_after_default_tags_block():
    files = {
        "main.tf": 'provider "aws" {\n  region = "us-west-2"\n  default_tags {\n'
        '    tags = { a = "b" }\n  }\n  alias = "late"\n}\n'
    }
    assert FlociExecutor.find_aliases(files) == ("late",)


def test_alias_inside_nested_block_is_ignored():
    files = {"main.tf": 'provider "aws" {\n  default_tags {\n    alias = "nope"\n  }\n}\n'}
    assert FlociExecutor.find_aliases(files) == ()


def test_classify_unsupported_vs_fail():
    cls = FlociExecutor.classify_error
    assert cls("Error: operation error: NotImplemented: CreateThing") is CheckStatus.UNSUPPORTED
    assert cls("api error UnknownOperation: Unknown operation") is CheckStatus.UNSUPPORTED
    assert cls("dial tcp: lookup foo: no such host") is CheckStatus.FAIL  # infra, não emulador
    assert cls("StatusCode: 501, RequestID: x") is CheckStatus.UNSUPPORTED
    assert cls("Error: InvalidParameterValue: bad name") is CheckStatus.FAIL


def test_apply_classifies_and_keeps_raw(tmp_path: Path):
    tc = FakeToolchain(1, "Error: creating X: NotImplemented: nope")
    res = _executor(tc).apply(Workspace(tmp_path, "h"))
    assert res.status is CheckStatus.UNSUPPORTED
    assert "NotImplemented" in res.raw
    assert tc.calls[0][:2] == ["terraform", "apply"]


def test_apply_uses_tfplan_when_present(tmp_path: Path):
    (tmp_path / "tfplan").write_text("x")
    tc = FakeToolchain()
    res = _executor(tc).apply(Workspace(tmp_path, "h"))
    assert res.status is CheckStatus.PASS
    assert tc.calls[0][-1] == "tfplan"


def test_apply_generic_failure_is_fail(tmp_path: Path):
    res = _executor(FakeToolchain(1, "Error: InvalidParameterValue")).apply(
        Workspace(tmp_path, "h")
    )
    assert res.status is CheckStatus.FAIL


class _Resp:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code, self.text = status_code, text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_reset(monkeypatch, vpc_text: str = "<isDefault>true</isDefault>"):
    """Troca docker/rede por fakes; devolve as chamadas registradas."""
    calls: dict[str, list] = {"run": [], "get": [], "post": []}
    health = iter([_Resp(503), _Resp(200)])  # a primeira consulta falha: Floci ainda subindo

    def fake_run(cmd, **kw):
        calls["run"].append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_get(url, **kw):
        calls["get"].append(url)
        return next(health)

    def fake_post(url, **kw):
        calls["post"].append((url, kw["headers"]["Authorization"], kw["data"]))
        return _Resp(200, vpc_text)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(httpx2, "get", fake_get)
    monkeypatch.setattr(httpx2, "post", fake_post)
    monkeypatch.setattr("iac_harness.executors.floci.time.sleep", lambda s: None)
    return calls


def test_reset_restarts_waits_health_and_seeds_vpcs(monkeypatch):
    calls = _patch_reset(monkeypatch)
    _executor().reset()
    assert calls["run"] == [["docker", "restart", "iac-harness-floci-1"]]
    assert calls["get"] == ["http://127.0.0.1:4566/_floci/health"] * 2
    assert len(calls["post"]) == len(DEFAULT_VPC_REGIONS)
    for (url, auth, body), region in zip(calls["post"], DEFAULT_VPC_REGIONS, strict=True):
        assert url == "http://127.0.0.1:4566/"
        assert f"/{region}/ec2/aws4_request" in auth
        assert body == {"Action": "DescribeVpcs", "Version": "2016-11-15"}  # form, não content


def test_reset_fails_loudly_without_default_vpc(monkeypatch):
    _patch_reset(monkeypatch, vpc_text="<isDefault>false</isDefault>")
    with pytest.raises(RuntimeError, match="VPC default"):
        _executor().reset()


def test_reset_restart_failure_includes_stderr(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "No such container: x")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="No such container: x"):
        _executor().reset()
