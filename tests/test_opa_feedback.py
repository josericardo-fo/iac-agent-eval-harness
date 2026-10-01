import json
import subprocess
from pathlib import Path

from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckStatus, TaskOracle
from iac_harness.verifiers.opa import OpaVerifier, extract_rule_names
from iac_harness.workspace import Workspace

# Exemplos de política do protocolo (IaC-Eval v2).
DAX_POLICY = """package terraform.policy

import rego.v1

default allow = false

# Check for DAX cluster creation
dax_cluster_created(resources) if {
some resource in resources
resource.type == "aws_dax_cluster"
resource.change.actions[_] == "create"
}

allow if {
dax_cluster_created(input.resource_changes)
}

main if {
    allow
}
"""

MSK_POLICY = """package terraform.policy

import rego.v1

default broker_node_count_valid := false
default allow := false

broker_node_count_valid if {
    some r in input.resource_changes
    r.change.after.number_of_broker_nodes == 3
}

allow if {
    broker_node_count_valid
    count_ok = 1
}

helper(x) := x

main if { allow }
"""


def test_extract_rule_names_skips_functions_main_and_default():
    assert extract_rule_names(DAX_POLICY) == ["allow"]
    assert extract_rule_names(MSK_POLICY) == ["broker_node_count_valid", "allow"]


class FakeToolchain(Toolchain):
    """Simula `opa eval`: responde por consulta, sem binário."""

    def __init__(self, answers: dict[str, object]) -> None:
        super().__init__(mode="local")
        self.answers = answers
        self.queries: list[str] = []

    def run(self, args, workdir, timeout_s=None):
        query = args[args.index("--format") - 1]
        self.queries.append(query)
        value = self.answers.get(query)
        result = [] if value is None else [{"expressions": [{"value": value}]}]
        out = json.dumps({"result": result} if result else {})
        return subprocess.CompletedProcess(args, 0, out, "")


def _workspace(tmp_path: Path) -> Workspace:
    (tmp_path / "plan.json").write_text("{}")
    return Workspace(tmp_path, "h")


ORACLE = TaskOracle({}, MSK_POLICY)


def test_main_true_passes(tmp_path):
    tc = FakeToolchain({"data.terraform.policy.main": True})
    res = OpaVerifier(tc).check(_workspace(tmp_path), ORACLE)
    assert res.status is CheckStatus.PASS


def test_rule_names_lists_unmet_rules(tmp_path):
    tc = FakeToolchain(
        {
            "data.terraform.policy.main": None,  # indefinido = FAIL
            "data.terraform.policy": {"allow": False, "broker_node_count_valid": False},
        }
    )
    res = OpaVerifier(tc, "rule_names").check(_workspace(tmp_path), ORACLE)
    assert res.status is CheckStatus.FAIL
    assert res.message == "Unsatisfied intent rules: broker_node_count_valid, allow"


def test_rule_names_omits_satisfied_rules(tmp_path):
    tc = FakeToolchain(
        {
            "data.terraform.policy.main": False,
            "data.terraform.policy": {"allow": False, "broker_node_count_valid": True},
        }
    )
    res = OpaVerifier(tc, "rule_names").check(_workspace(tmp_path), ORACLE)
    assert res.message == "Unsatisfied intent rules: allow"


def test_none_visibility_does_not_leak(tmp_path):
    tc = FakeToolchain({"data.terraform.policy.main": False})
    res = OpaVerifier(tc, "none").check(_workspace(tmp_path), ORACLE)
    assert res.message == "The plan does not satisfy the intent specification."
    assert "broker" not in res.message


def test_full_visibility_includes_policy(tmp_path):
    tc = FakeToolchain({"data.terraform.policy.main": False})
    res = OpaVerifier(tc, "full").check(_workspace(tmp_path), ORACLE)
    assert MSK_POLICY in res.message


def test_policy_is_not_written_to_workspace(tmp_path):
    tc = FakeToolchain({"data.terraform.policy.main": True})
    OpaVerifier(tc).check(_workspace(tmp_path), ORACLE)
    assert not (tmp_path / "policy.rego").exists()


def test_missing_plan_json_is_error(tmp_path):
    res = OpaVerifier(FakeToolchain({})).check(Workspace(tmp_path, "h"), ORACLE)
    assert res.status is CheckStatus.ERROR
