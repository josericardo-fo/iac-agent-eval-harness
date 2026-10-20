"""Execução de terraform/opa: em container na rede `iac-sandbox` (padrão) ou local (dev)."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_IMAGE = "iac-harness-toolchain:latest"
DEFAULT_NETWORK = "iac-sandbox"
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_ENDPOINT = "http://floci:4566"
# floci2: segunda instância do Floci na mesma rede interna (configs/miac.toml).
DEFAULT_ALLOWED_HOSTS = ("floci", "floci2", "localhost", "127.0.0.1")
DEFAULT_REGION = "us-east-1"

# Variáveis do host que o modo local pode herdar (nada de credenciais).
_LOCAL_PASSTHROUGH = ("PATH", "HOME", "TF_CLI_CONFIG_FILE", "TF_PLUGIN_CACHE_DIR")


class EndpointNotAllowedError(ValueError):
    pass


def host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def check_endpoint_allowed(
    url: str, allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
) -> None:
    """Defesa em profundidade, além da rede Docker internal: nunca apontar para fora."""
    if host_of(url) not in allowed_hosts:
        raise EndpointNotAllowedError(
            f"endpoint recusado (host {host_of(url)!r} fora da allowlist {allowed_hosts}): {url}"
        )


class Toolchain:
    def __init__(
        self,
        mode: str = "docker",
        image: str = DEFAULT_IMAGE,
        network: str = DEFAULT_NETWORK,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        endpoint: str = DEFAULT_ENDPOINT,
        region: str = DEFAULT_REGION,
    ) -> None:
        if mode not in ("docker", "local"):
            raise ValueError(f"toolchain.mode inválido: {mode!r} (use 'docker' ou 'local')")
        check_endpoint_allowed(endpoint)
        self.endpoint = endpoint
        self.region = region
        self.mode = mode
        self.image = image
        self.network = network
        self.timeout_s = timeout_s

    def sandbox_env(self) -> dict[str, str]:
        """Variáveis de AWS/Terraform enviadas à ferramenta; credenciais do host nunca entram."""
        return {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": self.region,
            "AWS_ENDPOINT_URL": self.endpoint,
            "AWS_EC2_METADATA_DISABLED": "true",
            "TF_IN_AUTOMATION": "1",
        }

    def _local_env(self) -> dict[str, str]:
        """Env montado do zero: só o mínimo do host (sem os.environ inteiro) + sandbox_env."""
        env = {k: os.environ[k] for k in _LOCAL_PASSTHROUGH if k in os.environ}
        return {**env, **self.sandbox_env()}

    def run(
        self, args: Sequence[str], workdir: Path, timeout_s: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Roda `args` (ex.: ["terraform", "validate"]) com `workdir` como diretório de trabalho.

        Levanta subprocess.TimeoutExpired / OSError; quem chama decide o status do gate.
        """
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        workdir = Path(workdir).resolve()
        if self.mode == "local":
            return subprocess.run(
                list(args),
                cwd=workdir,
                env=self._local_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        name = f"iac-harness-{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", self.network,
            *(a for k, v in self.sandbox_env().items() for a in ("-e", f"{k}={v}")),
            "-v", f"{workdir}:/work",
            "-w", "/work",
            self.image, *args,
        ]  # fmt: skip
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Matar o cliente docker não para o container.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
            raise

    def versions(self, workdir: Path) -> dict[str, str]:
        """Versões efetivas de terraform e opa, para o run_config."""
        out: dict[str, str] = {}
        for tool, args in (("terraform", ["terraform", "version"]), ("opa", ["opa", "version"])):
            try:
                proc = self.run(args, workdir, timeout_s=120)
                first = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
                out[tool] = first or f"erro: {proc.stderr.strip()[:200]}"
            except (subprocess.TimeoutExpired, OSError) as exc:
                out[tool] = f"indisponível: {exc}"
        return out
