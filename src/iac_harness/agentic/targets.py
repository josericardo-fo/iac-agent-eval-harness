"""Alvo onde o agente age: Floci (emulador) ou AWS real. Embrulha os executores existentes.

O alvo diz o que o container do agente recebe: variáveis de ambiente (as secretas só pelo nome),
redes Docker e proxy de egresso.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from iac_harness.executors import final_cleanup
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import AWS_CREDENTIAL_VARS, Toolchain, host_of

SANDBOX_NETWORK = "iac-sandbox"
# Redes internal onde só o proxy de egresso tem saída (docker/compose.yaml, perfil "agent").
EGRESS_NETWORK = "iac-egress"
EGRESS_AWS_NETWORK = "iac-egress-aws"
EGRESS_PROXY = "http://egress-proxy:3128"  # allowlist: só o host do ANTHROPIC_BASE_URL
EGRESS_AWS_PROXY = "http://egress-proxy-aws:3128"  # allowlist: o gateway + .amazonaws.com


# Containers dos proxies (projeto `iac-harness` do docker/compose.yaml).
EGRESS_PROXY_CONTAINER = "iac-harness-egress-proxy-1"
EGRESS_AWS_PROXY_CONTAINER = "iac-harness-egress-proxy-aws-1"


class EgressProxyDownError(RuntimeError):
    """O proxy de egresso do alvo não está rodando; toda trajetória viraria AGENT_ERROR."""


def check_container_running(container: str) -> None:
    """Levanta EgressProxyDownError se `container` não estiver rodando (ou não existir)."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
        running = proc.returncode == 0 and proc.stdout.strip() == "true"
        reason = proc.stderr.strip() or f"State.Running={proc.stdout.strip()!r}"
    except (subprocess.TimeoutExpired, OSError) as exc:
        running, reason = False, str(exc)
    if not running:
        raise EgressProxyDownError(
            f"proxy de egresso {container} não está rodando ({reason}); "
            "suba com `docker compose --profile agent up -d` e retome com agent-resume"
        )


@dataclass(frozen=True)
class FlociWorker:
    """Floci de um worker do agent-run (serviço do docker/compose.yaml)."""

    host: str  # nome do serviço = host na rede iac-sandbox (também no allowlist da toolchain)
    host_port: int  # porta publicada no loopback do host

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:4566"

    @property
    def host_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.host_port}"

    @property
    def container(self) -> str:
        return f"iac-harness-{self.host}-1"  # <projeto do compose>-<serviço>-1


# Fonte única dos Floci dos workers (worker 0 = o Floci padrão; 1.. = perfil agent-workers).
FLOCI_WORKERS = tuple(
    FlociWorker(host, 4566 + i) for i, host in enumerate(("floci", "floci2", "floci3", "floci4"))
)


@dataclass(frozen=True)
class AgentEnv:
    plain: dict[str, str]  # vão no argv como NOME=valor (nada secreto)
    secret: dict[str, str] = field(default_factory=dict)  # só `-e NOME`; valor no env do cliente


class Target(Protocol):
    kind: str
    toolchain: Toolchain
    executor: FlociExecutor

    def reset(self) -> None: ...

    def after_trajectory(self) -> None: ...

    def check_proxy(self) -> None:
        """Levanta EgressProxyDownError se o proxy de egresso do alvo não estiver rodando."""
        ...

    def agent_env(self) -> AgentEnv: ...

    def agent_networks(self) -> list[str]: ...

    def cleanup(self) -> None:
        """Limpeza ao fim do worker (num `finally`); não mascara a exceção original."""
        ...


def _proxy_env(proxy: str, no_proxy: str) -> dict[str, str]:
    # Maiúsculas e minúsculas: curl só lê http_proxy em minúsculas; Node/boto3 leem as duas.
    return {
        "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
        "NO_PROXY": no_proxy, "no_proxy": no_proxy,
    }  # fmt: skip


class FlociTarget:
    kind = "floci"

    def __init__(self, toolchain: Toolchain, executor: FlociExecutor) -> None:
        if toolchain.target != "emulator":
            raise ValueError("FlociTarget exige toolchain com target 'emulator'")
        self.toolchain = toolchain
        self.executor = executor

    def reset(self) -> None:
        self.executor.reset()

    def after_trajectory(self) -> None:
        """Nada: o próximo reset reinicia o emulador."""

    def check_proxy(self) -> None:
        check_container_running(EGRESS_PROXY_CONTAINER)

    def agent_env(self) -> AgentEnv:
        """Credenciais `test` e endpoint do Floci (as do sandbox_env); nunca as IAC_AWS_*."""
        no_proxy = f"{host_of(self.toolchain.endpoint)},localhost,127.0.0.1"
        return AgentEnv({**self.toolchain.sandbox_env(), **_proxy_env(EGRESS_PROXY, no_proxy)})

    def agent_networks(self) -> list[str]:
        return [SANDBOX_NETWORK, EGRESS_NETWORK]

    def cleanup(self) -> None:
        final_cleanup(self.executor)


class AwsTarget:
    kind = "aws"

    def __init__(self, toolchain: Toolchain, executor: RealAwsExecutor) -> None:
        self.toolchain = toolchain
        self.executor = executor

    def reset(self) -> None:
        """Confere o ARN de novo (sem o cache do executor) e limpa a conta contra o baseline."""
        # identity() guarda o resultado em cache; aqui a guarda vale por trajetória.
        self.executor.forget_identity()
        self.executor.identity()
        self.executor.reset()

    def after_trajectory(self) -> None:
        """Sweep contra o baseline logo depois da trajetória (não espera o próximo reset)."""
        self.executor.cleanup()

    def check_proxy(self) -> None:
        check_container_running(EGRESS_AWS_PROXY_CONTAINER)

    def agent_env(self) -> AgentEnv:
        """As IAC_AWS_* do host, renomeadas para AWS_*, só pelo nome; sem AWS_ENDPOINT_URL."""
        env = self.toolchain.sandbox_env()
        secret = {k: env.pop(k) for k in AWS_CREDENTIAL_VARS}
        return AgentEnv({**env, **_proxy_env(EGRESS_AWS_PROXY, "localhost,127.0.0.1")}, secret)

    def agent_networks(self) -> list[str]:
        return [EGRESS_AWS_NETWORK]

    def cleanup(self) -> None:
        final_cleanup(self.executor)
