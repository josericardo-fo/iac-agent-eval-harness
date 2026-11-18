import pytest

from iac_harness.cli import build_executor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain


def test_build_executor_picks_class_by_sandbox_kind():
    tc = Toolchain(mode="local")
    default = build_executor({}, tc)
    assert type(default) is FlociExecutor and default.container == "iac-harness-floci-1"
    assert type(build_executor({"sandbox": {"kind": "floci"}}, tc)) is FlociExecutor
    with pytest.raises(SystemExit, match="kind desconhecido"):
        build_executor({"sandbox": {"kind": "moto"}}, tc)
