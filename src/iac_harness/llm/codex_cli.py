"""Backend Codex CLI (`codex exec`, login do ChatGPT) do `TextModel`.

Isolado da config pessoal do usuário: o Codex roda com `HOME` e `CODEX_HOME` temporários (sem
config.toml, hooks, skills, plugins nem memórias) e só o login é reaproveitado, por um symlink
para o `auth.json` real (o token nunca é copiado nem impresso). O login real é só lido: nada
é gravado em `~/.codex`. Limitação: se o Codex renovar o token durante uma chamada, a renovação
se perde junto com o diretório temporário (rode `codex login` de novo se o login expirar).
Sandbox somente leitura, cwd vazio e sessão efêmera.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from functools import cached_property
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_TIMEOUT_S = 300
AUTH_FILENAME = "auth.json"
OUTPUT_FILENAME = "last_message.txt"
# Recursos do Codex que não servem a uma chamada só de texto (somam-se ao isolamento do HOME).
DISABLED_FEATURES = (
    "hooks", "plugins", "memories", "apps", "multi_agent", "shell_tool", "image_generation",
    "goals", "computer_use", "browser_use",
)  # fmt: skip
# Do ambiente do host, só o necessário para achar o binário e sair pela rede (nenhum segredo).
_ENV_PASSTHROUGH = (
    "PATH", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY",
    "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
)  # fmt: skip


def real_auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / AUTH_FILENAME


class CodexText:
    provider = "codex"

    def __init__(
        self, model: str = DEFAULT_MODEL, reasoning_effort: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S, codex_bin: str = "codex",
        auth_path: Path | None = None,
    ) -> None:  # fmt: skip
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_s = timeout_s
        self.codex_bin = codex_bin
        self.auth_path = auth_path if auth_path is not None else real_auth_path()

    @cached_property
    def codex_version(self) -> str:
        """Saída de `codex --version` (tolerante a falha, para a proveniência)."""
        try:
            proc = subprocess.run(
                [self.codex_bin, "--version"], capture_output=True, text=True, timeout=60
            )
            return proc.stdout.strip() or f"erro: {proc.stderr.strip()[:200]}"
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"indisponível: {exc}"

    @property
    def params(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model,
            "reasoning_effort": self.reasoning_effort, "codex_version": self.codex_version,
            "sandbox": "read-only",
        }  # fmt: skip

    def argv(self, cwd: Path, output: Path) -> list[str]:
        """Argv do `codex exec`; o prompt vai pelo stdin (`-`), fora do argv."""
        argv = [
            self.codex_bin, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
            "--ignore-rules", "--sandbox", "read-only", "--model", self.model, "--cd", str(cwd),
            "--output-last-message", str(output), "--color", "never",
        ]  # fmt: skip
        for feature in DISABLED_FEATURES:
            argv += ["-c", f"features.{feature}=false"]
        argv += ["-c", "skills.bundled.enabled=false", "-c", 'web_search="disabled"']
        if self.reasoning_effort:
            argv += ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
        return [*argv, "-"]

    def complete(self, system: str, user: str) -> str:
        """O Codex não tem campo de system: ele vai como prefixo do prompt."""
        if not self.auth_path.is_file():
            raise RuntimeError(f"codex sem login: {self.auth_path} não existe (rode `codex login`)")
        with tempfile.TemporaryDirectory(prefix="iac-codex-") as tmp:
            root = Path(tmp)
            home, cwd, codex_home = root / "home", root / "cwd", root / "codex-home"
            for d in (home, cwd, codex_home):
                d.mkdir()
            (codex_home / AUTH_FILENAME).symlink_to(self.auth_path)
            output = root / OUTPUT_FILENAME
            env = {k: v for k, v in os.environ.items() if k in _ENV_PASSTHROUGH}
            env.update(HOME=str(home), CODEX_HOME=str(codex_home))
            proc = subprocess.run(
                self.argv(cwd, output),
                input=f"{system.rstrip()}\n\n{user}",
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=env,
                cwd=cwd,
            )
            text = output.read_text(encoding="utf-8").strip() if output.is_file() else ""
        if proc.returncode != 0 or not text:
            raise RuntimeError(
                f"codex exec falhou (código {proc.returncode}): {proc.stderr.strip()[-500:]}"
            )
        return text
