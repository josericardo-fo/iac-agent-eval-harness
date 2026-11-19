"""Toolkits: o mesmo agente; muda só o que entra no contexto (plugins e regras).

O registro vem de `toolkit/toolkits.toml` (nome → plugin e regras); toolkit novo é dado, não
código. Cada toolkit vira montagens somente leitura e flags do `claude`. Os diretórios vêm de
`toolkit/` e `third_party/`, relativos a `plugin_root` (a raiz do repositório).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

CONTAINER_DIR = "/opt/conditions"
TOOLKITS_FILE = Path(__file__).resolve().parents[3] / "toolkit" / "toolkits.toml"


@cache
def load_toolkits(path: Path = TOOLKITS_FILE) -> dict[str, tuple[str | None, str | None]]:
    """Registro de toolkits: nome → (diretório do plugin, arquivo de regras), relativos ou None."""
    data = tomllib.loads(path.read_text())
    return {name: (t.get("plugin"), t.get("rules")) for name, t in data.items()}


@dataclass(frozen=True)
class Toolkit:
    name: str
    mounts: tuple[tuple[Path, str], ...]  # (caminho no host, caminho no container), sempre :ro
    flags: tuple[str, ...]
    plugin: str | None  # nome que deve aparecer em system/init.plugins

    def loaded(self, init: dict[str, Any] | None) -> bool:
        """O plugin do toolkit carregou (system/init) e expôs ao menos uma skill."""
        if self.plugin is None:
            return True
        if not init:
            return False
        names = {p.get("name") for p in init.get("plugins") or [] if isinstance(p, dict)}
        skills = [s for s in init.get("skills") or [] if isinstance(s, str)]
        return self.plugin in names and any(s.startswith(f"{self.plugin}:") for s in skills)


def resolve_toolkit(name: str, plugin_root: Path) -> Toolkit:
    """Monta o toolkit; diretório ou arquivo ausente levanta FileNotFoundError."""
    toolkits = load_toolkits()
    if name not in toolkits:
        raise ValueError(f"condição desconhecida: {name!r} (use uma de {sorted(toolkits)})")
    plugin_rel, prompt_rel = toolkits[name]
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
    return Toolkit(name, tuple(mounts), tuple(flags), plugin)
