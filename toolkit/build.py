"""Monta os plugins do toolkit a partir de `toolkit/src/` (fonte única).

- `own-rules`: só a skill (fluxo + regras);
- `own-tools`: a mesma skill com a seção de ferramentas e os scripts;
- `own-rules-v2`: como `own-rules`, a partir de `toolkit/src-v2/` (texto revisado, registro E11).

Os dois levam também `RULES.md`: regras curtas que o harness injeta no system prompt (como o
aws-core faz com `rules/`), para que o agente carregue a skill.

Uso: `uv run python toolkit/build.py` (reescreve `toolkit/plugins/`). Um teste confere que a
saída versionada está em dia com a fonte.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "src"
SRC_V2 = HERE / "src-v2"
OUT = HERE / "plugins"
SKILL_NAME = "iac-cloud"
VERSION = "0.2.0"

PLUGINS = {
    "own-rules": "Flow and rules for creating, changing, deleting and querying AWS infrastructure.",
    "own-tools": "Flow and rules for AWS infrastructure work, plus inventory, verify, "
    "diff-baseline and whoami scripts.",
    "own-rules-v2": "Flow and rules for creating, changing, deleting and querying AWS "
    "infrastructure.",
}


def tools_section() -> str:
    return (SRC / "TOOLS.md").read_text()


def skill_text(with_tools: bool, src: Path = SRC) -> str:
    return (src / "SKILL.md").read_text().replace("{tools}", tools_section() if with_tools else "")


def _manifest(name: str) -> str:
    data = {
        "name": name,
        "version": VERSION,
        "description": PLUGINS[name],
        "author": {"name": "iac-agent-eval-harness"},
    }
    return json.dumps(data, indent=2) + "\n"


def build(out: Path = OUT) -> None:
    for name in PLUGINS:
        root = out / name
        if root.exists():
            shutil.rmtree(root)
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(_manifest(name))
        skill_dir = root / "skills" / SKILL_NAME
        skill_dir.mkdir(parents=True)
        with_tools = name == "own-tools"
        src = SRC_V2 if name == "own-rules-v2" else SRC
        (skill_dir / "SKILL.md").write_text(skill_text(with_tools, src))
        shutil.copyfile(src / "RULES.md", root / "RULES.md")
        if with_tools:
            shutil.copytree(
                SRC / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
            )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
