"""Executor Floci: reset do emulador AWS local e override de provider."""

from __future__ import annotations

import re
import subprocess
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx2

from iac_harness.shell import query
from iac_harness.toolchain import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_ENDPOINT,
    EndpointNotAllowedError,
    Toolchain,
    check_endpoint_allowed,
)
from iac_harness.types import CheckStatus

if TYPE_CHECKING:
    from iac_harness.agentic.targets import FlociTarget

__all__ = ["EndpointNotAllowedError", "FlociExecutor"]

# Lista versionada de padrões "o emulador não implementa isso". Mudou? Suba a versão.
UNSUPPORTED_PATTERNS_VERSION = "1"
UNSUPPORTED_PATTERNS = (
    r"not implemented",
    r"notimplemented",
    r"unknownoperation",
    r"invalidaction",
    r"unsupported operation",
    r"unsupportedoperation",
    r"status\s?code:?\s*501",
)
_UNSUPPORTED = re.compile("|".join(UNSUPPORTED_PATTERNS), re.IGNORECASE)

# Serviços usados no dataset (chaves do bloco `endpoints` do provider aws 6.x).
ENDPOINT_SERVICES = (
    "s3", "iam", "sts", "ec2", "dynamodb", "lambda", "rds", "elasticache", "kafka",
    "kafkaconnect", "kinesis", "redshift", "sagemaker", "lightsail", "lexmodels", "lexv2models",
    "glacier", "dax", "kms", "eks", "cloudwatch", "cloudwatchlogs", "sns", "sqs", "efs",
    "route53", "secretsmanager", "ssm", "apigateway", "cloudfront", "ecr", "ecs", "elbv2",
    "autoscaling", "events", "cloudformation", "acm", "wafv2", "sfn", "athena", "glue",
)  # fmt: skip

# Uma conta AWS nova tem VPC default em todas as regiões (as 17 comerciais padrão, sem opt-in).
# O agente pode escolher qualquer uma, então o estado inicial cobre todas.
DEFAULT_VPC_REGIONS = (
    "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ap-south-1", "ap-northeast-1",
    "ap-northeast-2", "ap-northeast-3", "ap-southeast-1", "ap-southeast-2", "ca-central-1",
    "eu-central-1", "eu-west-1", "eu-west-2", "eu-west-3", "eu-north-1", "sa-east-1",
)  # fmt: skip
DEFAULT_HOST_ENDPOINT = "http://127.0.0.1:4566"
DEFAULT_CONTAINER = "iac-harness-floci-1"
RESTART_TIMEOUT_S = 60.0
HEALTH_TIMEOUT_S = 60.0

_PROVIDER_AWS_OPEN = re.compile(r'provider\s+"aws"\s*\{')
# Artefato usa o provider awscc (Cloud Control): resource/data `awscc_*` ou bloco `provider`.
_USES_AWSCC = re.compile(r'^\s*(?:(?:resource|data)\s+"awscc_|provider\s+"awscc")', re.MULTILINE)
# Chaves do atributo `endpoints` do provider awscc 1.x.
AWSCC_ENDPOINT_SERVICES = ("cloudcontrolapi", "iam", "sts")
_ALIAS = re.compile(r'^\s*alias\s*=\s*"([^"]+)"', re.MULTILINE)


def _top_level_alias(body: str) -> str | None:
    """Alias no nível 1 de um corpo de bloco (já sem a `{` de abertura), contando chaves."""
    depth = 1
    level1: list[str] = []  # texto do nível 1, sem o conteúdo de sub-blocos
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1:
            level1.append(ch)
    m = _ALIAS.search("".join(level1))
    return m.group(1) if m else None


def _emulator_image(container: str) -> tuple[str | None, str | None, str | None]:
    """Imagem, ID e RepoDigests do container do emulador (tolerante a falha, como `query`)."""
    image = query(["docker", "inspect", "--format", "{{.Config.Image}}", container])
    image_id = query(["docker", "inspect", "--format", "{{.Image}}", container])
    digests = None
    if image_id and not image_id.startswith("erro:"):
        digests = query(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id]
        )
    return image, image_id, digests


class FlociExecutor:
    """Backend Floci (emulador local); ver `executors.Backend`."""

    KIND = "floci"  # valor de `[sandbox] kind` (cli.build_executor) e da procedência
    NAME = "Floci"
    TOOLCHAIN_TARGET = "emulator"
    HEALTH_PATH = "/_floci/health"

    def __init__(
        self,
        toolchain: Toolchain,
        endpoint: str = DEFAULT_ENDPOINT,
        allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS,
        host_endpoint: str = DEFAULT_HOST_ENDPOINT,
        container: str = DEFAULT_CONTAINER,
    ) -> None:
        for url in (endpoint, host_endpoint):
            check_endpoint_allowed(url, allowed_hosts)
        self.endpoint = endpoint
        # O harness (no host) fala com 127.0.0.1:4566; a toolchain (no container), com floci:4566.
        self.host_endpoint = host_endpoint.rstrip("/")
        self.container = container
        self._tc = toolchain

    def override_tf(self, aliases: tuple[str, ...] = (), awscc: bool = False) -> str:
        """Conteúdo de zz_harness_override.tf.

        Não define `region`: o merge do override substituiria a região declarada pelo artefato
        (ex.: zonas `eu-west-1a` passariam a falhar no plan). Sem região declarada, vale o
        `AWS_REGION` que a toolchain injeta.

        Arquivos `*_override.tf` fazem merge com o bloco `provider "aws"` de mesmo nome/alias.
        Para o provider sem alias isso basta. Providers com alias não são cobertos por esse
        merge, então geramos um bloco override para cada alias encontrado no artefato
        (mesmo alias); ver `find_aliases`.

        Com `awscc`, acrescenta `provider "awscc"` apontando o Cloud Control para o Floci. Um bloco
        override sem bloco base no artefato é aceito pelo Terraform (vira a config do provider);
        a região também vem do `AWS_REGION`. Só é emitido quando o artefato usa awscc, para não
        mudar o override das tarefas que só usam aws.
        """
        endpoints = "\n".join(f'    {s} = "{self.endpoint}"' for s in ENDPOINT_SERVICES)
        block = (
            'provider "aws" {{\n{alias}'
            '  access_key                  = "test"\n'
            '  secret_key                  = "test"\n'
            "  skip_credentials_validation = true\n"
            "  skip_requesting_account_id  = true\n"
            "  skip_metadata_api_check     = true\n"
            "  s3_use_path_style           = true\n"
            "  endpoints {{\n{endpoints}\n  }}\n"
            "}}\n"
        )
        blocks = [block.format(alias="", endpoints=endpoints)]
        for alias in aliases:
            line = f'  alias                       = "{alias}"\n'
            blocks.append(block.format(alias=line, endpoints=endpoints))
        if awscc:
            cc_endpoints = "\n".join(
                f'    {s:<15} = "{self.endpoint}"' for s in AWSCC_ENDPOINT_SERVICES
            )
            blocks.append(
                'provider "awscc" {\n'
                '  access_key              = "test"\n'
                '  secret_key              = "test"\n'
                "  skip_metadata_api_check = true\n"
                f"  endpoints = {{\n{cc_endpoints}\n  }}\n"
                "}\n"
            )
        header = "# Gerado pelo harness: aponta todos os endpoints AWS para o Floci.\n"
        return header + "\n".join(blocks)

    @staticmethod
    def find_aliases(files: dict[str, str]) -> tuple[str, ...]:
        """Aliases de `provider "aws"` declarados nos arquivos .tf do artefato."""
        found: list[str] = []
        for name, body in files.items():
            if not name.endswith(".tf"):
                continue
            for m in _PROVIDER_AWS_OPEN.finditer(body):
                alias = _top_level_alias(body[m.end() :])
                if alias and alias not in found:
                    found.append(alias)
        return tuple(found)

    @staticmethod
    def uses_awscc(files: dict[str, str]) -> bool:
        """Algum .tf do artefato usa o provider awscc."""
        return any(n.endswith(".tf") and _USES_AWSCC.search(body) for n, body in files.items())

    def override_for(self, files: dict[str, str]) -> str:
        return self.override_tf(self.find_aliases(files), self.uses_awscc(files))

    @staticmethod
    def classify_error(output: str) -> CheckStatus:
        return CheckStatus.UNSUPPORTED if _UNSUPPORTED.search(output) else CheckStatus.FAIL

    def reset(self) -> None:
        """Devolve o sandbox ao estado de "conta AWS nova": restart do container + VPCs default.

        O `POST /_floci/state/reset` do Floci 2.1.0 apaga a VPC default e as subnets default e não
        as recria (protocolo §9.2), então o reset é um restart do container (~1 s). Falha de
        propósito com exceção: uma pré-validação com sandbox sujo não pode passar despercebida.
        """
        self._restart()
        self._seed_default_vpcs()

    def cleanup(self) -> None:
        """Limpeza ao fim do run: nada a fazer no emulador (o próximo reset reinicia)."""

    def provenance(self) -> dict[str, Any]:
        """Tipo, imagem, ID e RepoDigests do container do emulador."""
        image, image_id, digests = _emulator_image(self.container)
        return {
            "sandbox_kind": self.KIND,
            "sandbox_image": image,
            "sandbox_image_id": image_id,
            "sandbox_repo_digests": digests,
        }

    def agent_target(self) -> FlociTarget:
        # Import local: agentic.targets importa os executores.
        from iac_harness.agentic.targets import FlociTarget

        return FlociTarget(self._tc, self)

    def _restart(self) -> None:
        """`docker restart` do container e espera o health (`HEALTH_PATH`) responder 200."""
        proc = subprocess.run(
            ["docker", "restart", self.container],
            capture_output=True, text=True, timeout=RESTART_TIMEOUT_S,
        )  # fmt: skip
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker restart {self.container} falhou ({proc.returncode}): {proc.stderr.strip()}"
            )
        self._wait_healthy()

    def _wait_healthy(self) -> None:
        url = f"{self.host_endpoint}{self.HEALTH_PATH}"
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while True:
            try:
                if httpx2.get(url, timeout=5).status_code == 200:
                    return
            except httpx2.HTTPError:
                pass  # ainda subindo
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{self.NAME} não ficou saudável em {HEALTH_TIMEOUT_S:.0f}s: {url}"
                )
            time.sleep(0.5)

    def _seed_default_vpcs(self) -> None:
        """Cria a VPC default de cada região: o Floci só a cria na primeira chamada EC2 à região."""
        day = datetime.now(UTC).strftime("%Y%m%d")
        for region in DEFAULT_VPC_REGIONS:
            # O Floci tira a região do escopo da credencial e não valida a assinatura.
            auth = (
                f"AWS4-HMAC-SHA256 Credential=test/{day}/{region}/ec2/aws4_request, "
                "SignedHeaders=host, Signature=x"
            )
            resp = httpx2.post(
                f"{self.host_endpoint}/",
                headers={"Authorization": auth},
                data={"Action": "DescribeVpcs", "Version": "2016-11-15"},  # form-urlencoded
                timeout=30,
            )
            resp.raise_for_status()
            if "<isDefault>true</isDefault>" not in resp.text:
                raise RuntimeError(f"VPC default não encontrada em {region} após o reset")
