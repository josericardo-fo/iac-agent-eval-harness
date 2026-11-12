"""Toolkit (toolkit/): build dos plugins, texto da skill e lógica dos scripts, sem AWS."""

from __future__ import annotations

import filecmp
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TOOLKIT = ROOT / "toolkit"
PLUGINS = TOOLKIT / "plugins"
SCRIPTS = TOOLKIT / "src" / "scripts"
HARNESS_TERMS = ("floci", "check.py", "answer.json", "harness", "experiment")
SCRIPT_NAMES = ("whoami", "inventory", "verify", "diff_baseline")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build():
    return _load("toolkit_build", TOOLKIT / "build.py")


@pytest.fixture(scope="module")
def scripts():
    # Os scripts importam `_common` do próprio diretório, como quando rodam pelo Bash.
    sys.path.insert(0, str(SCRIPTS))
    try:
        yield {n: _load(f"toolkit_{n}", SCRIPTS / f"{n}.py") for n in SCRIPT_NAMES}
    finally:
        sys.path.remove(str(SCRIPTS))


def _skill(plugin: str) -> str:
    return (PLUGINS / plugin / "skills" / "iac-cloud" / "SKILL.md").read_text()


def _assert_same_tree(a: Path, b: Path) -> None:
    cmp = filecmp.dircmp(a, b)
    assert not (cmp.left_only or cmp.right_only), (a, cmp.left_only, cmp.right_only)
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    assert not (mismatch or errors), (a, mismatch, errors)
    for sub in cmp.common_dirs:
        _assert_same_tree(a / sub, b / sub)


# --- build ---------------------------------------------------------------


def test_built_plugins_are_up_to_date(build, tmp_path):
    build.build(tmp_path)
    _assert_same_tree(tmp_path, PLUGINS)


def test_own_tools_differs_from_own_rules_only_by_tools_section(build):
    rules, tools = _skill("own-rules"), _skill("own-tools")
    section = build.tools_section()
    assert section in tools
    assert tools.replace(section, "") == rules
    assert "CLAUDE_PLUGIN_ROOT" not in rules


def test_own_tools_ships_every_script_and_skill_references_it():
    tools = _skill("own-tools")
    for name in SCRIPT_NAMES:
        assert (PLUGINS / "own-tools" / "scripts" / f"{name}.py").is_file()
        assert f"${{CLAUDE_PLUGIN_ROOT}}/scripts/{name}.py" in tools
    assert not (PLUGINS / "own-rules" / "scripts").exists()


@pytest.mark.parametrize("plugin", ["own-rules", "own-tools", "own-rules-v2"])
def test_plugin_manifest_is_valid(plugin):
    manifest = json.loads((PLUGINS / plugin / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == plugin
    for field in ("version", "description"):
        assert manifest[field]
    assert manifest["author"]["name"]


@pytest.mark.parametrize("plugin", ["own-rules", "own-tools", "own-rules-v2"])
def test_skill_has_frontmatter_and_no_harness_terms(plugin):
    text = _skill(plugin)
    assert text.startswith("---\nname: iac-cloud\ndescription: ")
    lowered = text.lower()
    for term in HARNESS_TERMS:
        assert term not in lowered, term


def test_rules_file_is_shipped_identically_and_has_no_harness_terms():
    src = (TOOLKIT / "src" / "RULES.md").read_text()
    for plugin in ("own-rules", "own-tools"):
        assert (PLUGINS / plugin / "RULES.md").read_text() == src
    lowered = src.lower()
    for term in HARNESS_TERMS:
        assert term not in lowered, term


def test_v2_ships_its_own_rules_and_no_tools():
    src = (TOOLKIT / "src-v2" / "RULES.md").read_text()
    assert (PLUGINS / "own-rules-v2" / "RULES.md").read_text() == src
    assert src != (TOOLKIT / "src" / "RULES.md").read_text()
    for term in HARNESS_TERMS:
        assert term not in src.lower(), term
    assert "CLAUDE_PLUGIN_ROOT" not in _skill("own-rules-v2")
    assert not (PLUGINS / "own-rules-v2" / "scripts").exists()


# --- scripts: --help -----------------------------------------------------


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_script_help_exits_zero_without_boto3(name):
    # --help não pode depender do boto3 (o ambiente de dev não o tem).
    proc = subprocess.run(
        [sys.executable, str(PLUGINS / "own-tools" / "scripts" / f"{name}.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout


# --- scripts: diff-baseline ----------------------------------------------


def _inv(resources, errors=()):
    return {"resources": resources, "errors": list(errors)}


def _res(rtype, rid, region="us-east-1", tags=None):
    return {"type": rtype, "id": rid, "region": region, "tags": tags or {}}


def test_diff_reports_added_and_removed(scripts):
    base = _inv([_res("s3.bucket", "keep", "global"), _res("sqs.queue", "old")])
    cur = _inv([_res("s3.bucket", "keep", "global"), _res("sqs.queue", "new")])
    out = scripts["diff_baseline"].diff(base, cur)
    assert out["added"] == [_res("sqs.queue", "new")]
    assert out["removed"] == [_res("sqs.queue", "old")]
    assert out["errors"] == []


def test_diff_keys_by_type_region_and_id(scripts):
    base = _inv([_res("ec2.vpc", "vpc-1", "us-east-1")])
    cur = _inv([_res("ec2.vpc", "vpc-1", "us-west-2"), _res("ec2.subnet", "vpc-1")])
    out = scripts["diff_baseline"].diff(base, cur)
    assert len(out["added"]) == 2
    assert out["removed"] == [_res("ec2.vpc", "vpc-1", "us-east-1")]


def test_diff_ignores_tag_changes_and_carries_errors(scripts):
    # Mudança só de tag não é recurso novo; erros de listagem tornam o diff parcial.
    err = {"service": "kafka", "region": "us-east-1", "error": "not supported"}
    base = _inv([_res("sqs.queue", "q", tags={"a": "1"})])
    cur = _inv([_res("sqs.queue", "q", tags={"a": "2"})], [err])
    out = scripts["diff_baseline"].diff(base, cur)
    assert out["added"] == [] and out["removed"] == []
    assert out["errors"] == [{"in": "current", **err}]


def test_diff_baseline_cli_with_current_file(tmp_path):
    base, cur = tmp_path / "base.json", tmp_path / "cur.json"
    base.write_text(json.dumps(_inv([])))
    cur.write_text(json.dumps(_inv([_res("sqs.queue", "q")])))
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff_baseline.py"), str(base), "--current", str(cur)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["added"] == [_res("sqs.queue", "q")]


def test_diff_baseline_cli_missing_file_fails_with_json(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff_baseline.py"), str(tmp_path / "nope.json"),
         "--current", str(tmp_path / "nope.json")],
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert proc.returncode != 0
    assert "error" in json.loads(proc.stdout)


# --- scripts: verify -----------------------------------------------------


class FakeClient:
    """Cliente boto3 falso: devolve as respostas em ordem (exceção = erro da chamada)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_queue_attributes(self, **params):
        self.calls.append(params)
        resp = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def _attrs(value):
    return {"Attributes": {"MessageRetentionPeriod": value}}


def test_verify_retries_until_expectation_holds(scripts):
    v = scripts["verify"]
    client = FakeClient([_attrs("60"), RuntimeError("throttled"), _attrs("345600")])
    clock = FakeClock()
    out = v.verify(
        client, "get-queue-attributes", {"QueueUrl": "u"},
        [v.parse_expect("Attributes.MessageRetentionPeriod=345600")],
        timeout=60, interval=5, clock=clock.time, sleep=clock.sleep,
    )  # fmt: skip
    assert out["ok"] is True
    assert out["attempts"] == 3
    assert client.calls[0] == {"QueueUrl": "u"}


def test_verify_times_out_with_last_observed_value(scripts):
    v = scripts["verify"]
    clock = FakeClock()
    out = v.verify(
        FakeClient([_attrs("60")]), "GetQueueAttributes", {},
        [v.parse_expect("Attributes.MessageRetentionPeriod=345600")],
        timeout=20, interval=5, clock=clock.time, sleep=clock.sleep,
    )  # fmt: skip
    assert out["ok"] is False
    assert out["attempts"] == 5  # t = 0, 5, 10, 15, 20
    assert out["mismatches"] == [
        {"path": "Attributes.MessageRetentionPeriod", "expected": 345600, "actual": "60"}
    ]


def test_verify_reports_last_error_on_timeout(scripts):
    v = scripts["verify"]
    clock = FakeClock()
    out = v.verify(
        FakeClient([RuntimeError("NoSuchQueue")]), "get_queue_attributes", {},
        [v.parse_expect("Attributes.X=1")],
        timeout=10, interval=5, clock=clock.time, sleep=clock.sleep,
    )  # fmt: skip
    assert out["ok"] is False
    assert "NoSuchQueue" in out["last_error"]


@pytest.mark.parametrize(
    ("doc", "path", "expected", "ok"),
    [
        ({"A": {"B": [{"C": "x"}]}}, "A.B[0].C", "x", True),
        ({"A": {"B": [{"C": "x"}]}}, "A.B[1].C", "x", False),
        ({"Table": {"Status": "ACTIVE"}}, "Table.Status", "ACTIVE", True),
        ({"N": 5}, "N", "5", True),
        ({"Flag": True}, "Flag", "true", True),
        ({"Items": []}, "Items", "[]", True),
    ],
)
def test_verify_path_matching(scripts, doc, path, expected, ok):
    # O valor esperado é lido como JSON quando possível e casa com a string da API ("5" == 5).
    v = scripts["verify"]
    assert (v.mismatches(doc, [v.parse_expect(f"{path}={expected}")]) == []) is ok


def test_verify_operation_name_normalisation(scripts):
    v = scripts["verify"]
    for op in ("describe-db-instances", "DescribeDBInstances", "describe_db_instances"):
        assert v.method_name(op) == "describe_db_instances"


# --- scripts: inventory (lógica pura) ------------------------------------


def test_inventory_tag_filter(scripts):
    inv = scripts["inventory"]
    rs = [_res("s3.bucket", "a", tags={"env": "dev"}), _res("s3.bucket", "b", tags=None)]
    assert inv.filter_by_tags(rs, {"env": "dev"}) == [rs[0]]
    assert inv.filter_by_tags(rs, {}) == rs


def test_inventory_collector_failure_is_recorded_and_others_continue(scripts):
    inv = scripts["inventory"]

    def ok(session, region):
        return [_res("sqs.queue", "q", region)]

    def boom(session, region):
        raise RuntimeError("service not supported")

    out = inv.collect(None, ["us-east-1"], {"sqs": (ok, False), "kafka": (boom, False)})
    assert out["resources"] == [_res("sqs.queue", "q")]
    assert out["errors"] == [
        {"service": "kafka", "region": "us-east-1", "error": "RuntimeError: service not supported"}
    ]


def test_inventory_global_collectors_run_once(scripts):
    inv = scripts["inventory"]
    calls = []

    def iam(session, region):
        calls.append(region)
        return []

    inv.collect(None, ["us-east-1", "eu-west-1"], {"iam": (iam, True)})
    assert calls == ["us-east-1"]


# --- distribuição --------------------------------------------------------


def test_distributed_plugin_is_the_measured_v2():
    """O `iac-cloud` instalável leva o mesmo texto da condição `own-rules-v2` (E11/E12)."""
    assert _skill("iac-cloud") == _skill("own-rules-v2")
    v2, dist = PLUGINS / "own-rules-v2", PLUGINS / "iac-cloud"
    assert (dist / "RULES.md").read_text() == (v2 / "RULES.md").read_text()
    hook = json.loads((dist / "hooks" / "hooks.json").read_text())
    command = hook["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert command == 'cat "${CLAUDE_PLUGIN_ROOT}/RULES.md"'


def test_marketplace_lists_the_distributed_plugin():
    root = TOOLKIT.parent
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    [entry] = market["plugins"]
    source = root / entry["source"]
    manifest = json.loads((source / ".claude-plugin" / "plugin.json").read_text())
    assert entry["name"] == manifest["name"] == "iac-cloud"
