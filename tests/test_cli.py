import pytest

from iac_harness.cli import _load_prevalidation, build_executor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.executors.localstack import LocalStackExecutor
from iac_harness.toolchain import Toolchain


def test_run_without_prevalidation_fails_unless_explicit(tmp_path):
    with pytest.raises(SystemExit):
        _load_prevalidation(None, allow_missing=False)
    assert _load_prevalidation(None, allow_missing=True) == {}


def test_build_executor_picks_class_by_sandbox_kind():
    tc = Toolchain(mode="local")
    default = build_executor({}, tc)
    assert type(default) is FlociExecutor and default.container == "iac-harness-floci-1"
    assert type(build_executor({"sandbox": {"kind": "floci"}}, tc)) is FlociExecutor
    ls = build_executor({"sandbox": {"kind": "localstack"}}, tc)
    assert type(ls) is LocalStackExecutor
    assert (ls.endpoint, ls.host_endpoint, ls.container) == (
        "http://localstack:4566", "http://127.0.0.1:4567", "iac-harness-localstack-1",
    )  # fmt: skip
    with pytest.raises(SystemExit, match="kind desconhecido"):
        build_executor({"sandbox": {"kind": "moto"}}, tc)
