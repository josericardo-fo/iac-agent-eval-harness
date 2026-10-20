"""Executor LocalStack: mesma interface do Floci, só muda URL, container e reset."""

from __future__ import annotations

from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import DEFAULT_ALLOWED_HOSTS, Toolchain

__all__ = ["LocalStackExecutor"]

DEFAULT_ENDPOINT = "http://localstack:4566"
# Porta 4567 no host, para não colidir com o Floci (docker/compose.yaml).
DEFAULT_HOST_ENDPOINT = "http://127.0.0.1:4567"
DEFAULT_CONTAINER = "iac-harness-localstack-1"


class LocalStackExecutor(FlociExecutor):
    KIND = "localstack"
    NAME = "LocalStack"
    HEALTH_PATH = "/_localstack/health"

    def __init__(
        self,
        toolchain: Toolchain,
        endpoint: str = DEFAULT_ENDPOINT,
        allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS,
        host_endpoint: str = DEFAULT_HOST_ENDPOINT,
        container: str = DEFAULT_CONTAINER,
    ) -> None:
        super().__init__(toolchain, endpoint, allowed_hosts, host_endpoint, container)

    def reset(self) -> None:
        """Restart do container + health. Sem seed: o LocalStack já cria a VPC default."""
        self._restart()
