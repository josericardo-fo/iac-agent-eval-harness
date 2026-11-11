"""Execução de terraform/opa: em container na rede `iac-sandbox` (padrão) ou local (dev).

Alvo `emulator` (padrão): credenciais `test` e endpoint do emulador. Alvo `aws`: credenciais
`IAC_AWS_*` do ambiente, sem endpoint, na rede `bridge` (com internet).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_IMAGE = "iac-harness-toolchain:latest"
DEFAULT_NETWORK = "iac-sandbox"
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_ENDPOINT = "http://floci:4566"
# floci2: segunda instância do Floci na mesma rede interna (configs/miac.toml); floci2..4 também
# servem aos workers paralelos do agente (configs/e3-agent.toml).
# localstack: emulador alternativo (configs/modality-spike-localstack.toml).
DEFAULT_ALLOWED_HOSTS = (
    "floci", "floci2", "floci3", "floci4", "localstack", "localhost", "127.0.0.1",
)  # fmt: skip
DEFAULT_REGION = "us-east-1"
TARGETS = ("emulator", "aws")
# Alvo aws: rede com internet e credenciais vindas destas variáveis do host.
AWS_NETWORK = "bridge"
AWS_CREDENTIAL_VARS = {
    "AWS_ACCESS_KEY_ID": "IAC_AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY": "IAC_AWS_SECRET_ACCESS_KEY",
}

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
        target: str = "emulator",
    ) -> None:
        if mode not in ("docker", "local"):
            raise ValueError(f"toolchain.mode inválido: {mode!r} (use 'docker' ou 'local')")
        if target not in TARGETS:
            raise ValueError(f"toolchain.target inválido: {target!r} (use um de {TARGETS})")
        if target == "aws":
            missing = [v for v in AWS_CREDENTIAL_VARS.values() if not os.environ.get(v)]
            if missing:
                raise RuntimeError(
                    f"alvo aws sem credenciais: defina {', '.join(missing)} "
                    "(ex.: set -a; source .env; set +a)"
                )
            network = AWS_NETWORK
        else:
            check_endpoint_allowed(endpoint)
        self.target = target
        self.endpoint = endpoint
        self.region = region
        self.mode = mode
        self.image = image
        self.network = network
        self.timeout_s = timeout_s

    def sandbox_env(self) -> dict[str, str]:
        """Variáveis de AWS/Terraform enviadas à ferramenta; credenciais do host nunca entram.

        No alvo aws, só entram as `IAC_AWS_*` (renomeadas), sem `AWS_ENDPOINT_URL`.
        """
        if self.target == "aws":
            return {
                **{k: os.environ[v] for k, v in AWS_CREDENTIAL_VARS.items()},
                "AWS_REGION": self.region,
                "AWS_DEFAULT_REGION": self.region,
                "AWS_EC2_METADATA_DISABLED": "true",
                "TF_IN_AUTOMATION": "1",
            }
        return {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": self.region,
            # O boto3 só lê AWS_DEFAULT_REGION (AWS CLI v2 e Terraform leem AWS_REGION).
            "AWS_DEFAULT_REGION": self.region,
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
        env = self.sandbox_env()
        # Segredo nunca na linha de comando: `-e NOME` e o valor no env do cliente docker.
        secrets = AWS_CREDENTIAL_VARS if self.target == "aws" else {}
        cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", self.network,
            *(a for k, v in env.items() for a in ("-e", k if k in secrets else f"{k}={v}")),
            "-v", f"{workdir}:/work",
            "-w", "/work",
            self.image, *args,
        ]  # fmt: skip
        kwargs = {"env": {**os.environ, **{k: env[k] for k in secrets}}} if secrets else {}
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired:
            # Matar o cliente docker não para o container.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
            raise

    def versions(self, workdir: Path) -> dict[str, str]:
        """Versões de terraform, opa, aws, boto3 e jq (+ id da imagem), para o run_config."""
        out: dict[str, str] = {}
        tools = (
            ("terraform", ["terraform", "version"]),
            ("opa", ["opa", "version"]),
            ("aws", ["aws", "--version"]),
            ("boto3", ["python3", "-c", "import boto3; print(boto3.__version__)"]),
            ("jq", ["jq", "--version"]),
        )
        for tool, args in tools:
            try:
                proc = self.run(args, workdir, timeout_s=120)
                first = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
                out[tool] = first or f"erro: {proc.stderr.strip()[:200]}"
            except (subprocess.TimeoutExpired, OSError) as exc:
                out[tool] = f"indisponível: {exc}"
        if self.mode == "docker":
            # ID da imagem (no host): aponta exatamente o ambiente usado no run.
            try:
                proc = subprocess.run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", self.image],
                    capture_output=True, text=True, timeout=120,
                )  # fmt: skip
                out["image_id"] = proc.stdout.strip() or f"erro: {proc.stderr.strip()[:200]}"
            except (subprocess.TimeoutExpired, OSError) as exc:
                out["image_id"] = f"indisponível: {exc}"
        return out


def _terraform_dirs(root: Path) -> list[Path]:
    """Todos os `.terraform` sob `root`, inclusive em subpastas, sem descer neles nem em links."""
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        if ".terraform" in dirnames:
            found.append(Path(dirpath) / ".terraform")
            dirnames.remove(".terraform")
    return sorted(found)


def remove_terraform_dir(path: Path, toolchain: Toolchain | None = None) -> None:
    """Remove todo `.terraform` sob `path` (cópia do provider, ~840 MB cada); nunca levanta.

    O agente pode rodar o Terraform numa subpasta de `work/`, então a busca é recursiva.
    Em Docker nativo no Linux os arquivos criados pelo container mantêm o dono (root/agent) e o
    `rmtree` do host falha em silêncio; nesse caso apaga de dentro de um container (root da imagem).
    Se ainda existir, avisa no stderr: a limpeza não pode mudar o resultado de uma trajetória.
    """
    root = Path(path)
    for target in _terraform_dirs(root):
        shutil.rmtree(target, ignore_errors=True)
        if target.exists() and toolchain is not None and toolchain.mode == "docker":
            rel = target.relative_to(root).as_posix()
            try:
                toolchain.run(["rm", "-rf", f"/work/{rel}"], root)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"warning: falha ao remover {target} via container: {exc}", file=sys.stderr)
        if target.exists():
            msg = f"warning: não foi possível remover {target} (disco pode encher)"
            print(msg, file=sys.stderr)
