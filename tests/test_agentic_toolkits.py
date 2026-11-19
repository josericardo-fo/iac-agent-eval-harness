"""Registro de toolkits (toolkit/toolkits.toml): os nomes medidos resolvem como antes."""

from pathlib import Path

import pytest

from iac_harness.agentic.conditions import load_toolkits, resolve_toolkit

ROOT = Path(__file__).parent.parent

# O dicionário que vivia em agentic/conditions.py antes do registro em dados.
MEASURED = {
    "none": (None, None),
    "aws-core": (
        "third_party/agent-toolkit-for-aws/plugins/aws-core",
        "third_party/agent-toolkit-for-aws/rules/aws-agent-rules.md",
    ),
    "own-skill-only": ("toolkit/plugins/own-rules", None),
    "own-rules": ("toolkit/plugins/own-rules", "toolkit/plugins/own-rules/RULES.md"),
    "own-tools": ("toolkit/plugins/own-tools", "toolkit/plugins/own-tools/RULES.md"),
    "own-rules-v2": ("toolkit/plugins/own-rules-v2", "toolkit/plugins/own-rules-v2/RULES.md"),
    "own-rules-v3": ("toolkit/plugins/own-rules-v3", "toolkit/plugins/own-rules-v3/RULES.md"),
}


def test_data_file_matches_measured_registry():
    assert load_toolkits() == MEASURED


@pytest.mark.parametrize("name", sorted(MEASURED))
def test_measured_toolkits_resolve_in_repo(name):
    tk = resolve_toolkit(name, ROOT)
    plugin_rel, rules_rel = MEASURED[name]
    hosts = [h for h, _ in tk.mounts]
    expected = [(ROOT / r).resolve() for r in (plugin_rel, rules_rel) if r is not None]
    assert tk.name == name and hosts == expected
    assert tk.plugin == (Path(plugin_rel).name if plugin_rel else None)


def test_toolkit_added_by_data(tmp_path):
    path = tmp_path / "toolkits.toml"
    path.write_text('[none]\n\n[mine]\nplugin = "p"\n')
    assert load_toolkits(path) == {"none": (None, None), "mine": ("p", None)}
