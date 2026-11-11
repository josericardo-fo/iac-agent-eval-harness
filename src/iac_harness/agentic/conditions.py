"""Condições do E3: o mesmo Claude Code; muda só o que entra no contexto (plugins e regras).

Cada condição vira montagens somente leitura e flags do `claude`. Os diretórios vêm de
`toolkit/` e `third_party/`, relativos a `plugin_root` (a raiz do repositório).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTAINER_DIR = "/opt/conditions"
AWS_CORE_DIR = "third_party/agent-toolkit-for-aws/plugins/aws-core"
AWS_RULES_FILE = "third_party/agent-toolkit-for-aws/rules/aws-agent-rules.md"

# nome → (plugin: diretório relativo, arquivo de system prompt relativo ou None)
REGISTRY: dict[str, tuple[str | None, str | None]] = {
    "none": (None, None),
    "aws-core": (AWS_CORE_DIR, AWS_RULES_FILE),
    # own-skill-only: só o plugin, sem regras no system prompt (no piloto E3: 0 chamadas de Skill)
    "own-skill-only": ("toolkit/plugins/own-rules", None),
    "own-rules": ("toolkit/plugins/own-rules", "toolkit/plugins/own-rules/RULES.md"),
    "own-tools": ("toolkit/plugins/own-tools", "toolkit/plugins/own-tools/RULES.md"),
    # own-rules-v2: texto revisado (registro E11); a v1 fica intacta
    "own-rules-v2": ("toolkit/plugins/own-rules-v2", "toolkit/plugins/own-rules-v2/RULES.md"),
}


@dataclass(frozen=True)
class Condition:
    name: str
    mounts: tuple[tuple[Path, str], ...]  # (caminho no host, caminho no container), sempre :ro
    flags: tuple[str, ...]
    plugin: str | None  # nome que deve aparecer em system/init.plugins

    def loaded(self, init: dict[str, Any] | None) -> bool:
        """O plugin da condição carregou (system/init) e expôs ao menos uma skill."""
        if self.plugin is None:
            return True
        if not init:
            return False
        names = {p.get("name") for p in init.get("plugins") or [] if isinstance(p, dict)}
        skills = [s for s in init.get("skills") or [] if isinstance(s, str)]
        return self.plugin in names and any(s.startswith(f"{self.plugin}:") for s in skills)


def resolve_condition(name: str, plugin_root: Path) -> Condition:
    """Monta a condição; diretório ou arquivo ausente levanta FileNotFoundError."""
    if name not in REGISTRY:
        raise ValueError(f"condição desconhecida: {name!r} (use uma de {sorted(REGISTRY)})")
    plugin_rel, prompt_rel = REGISTRY[name]
    mounts: list[tuple[Path, str]] = []
    flags: list[str] = []
    plugin = None
    if plugin_rel is not None:
        host = (plugin_root / plugin_rel).resolve()
        if not host.is_dir():
            raise FileNotFoundError(f"condição {name}: plugin ausente em {host}")
        plugin = host.name
        container = f"{CONTAINER_DIR}/{plugin}"
        mounts.append((host, container))
        flags += ["--plugin-dir", container]
    if prompt_rel is not None:
        host = (plugin_root / prompt_rel).resolve()
        if not host.is_file():
            raise FileNotFoundError(f"condição {name}: regras ausentes em {host}")
        container = f"{CONTAINER_DIR}/{host.name}"
        mounts.append((host, container))
        flags += ["--append-system-prompt-file", container]
    return Condition(name, tuple(mounts), tuple(flags), plugin)
