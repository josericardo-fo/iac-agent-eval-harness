"""Inventário e limpeza da conta AWS real (roda DENTRO da toolchain, com o boto3 da imagem).

Uso: `python3 -m aws_sweep inventory` imprime o inventário em JSON;
`python3 -m aws_sweep sweep baseline.json` apaga o que não está no baseline e imprime
`{"deleted": ..., "failed": ..., "remaining": ...}`.

Escopo: a região do `AWS_DEFAULT_REGION` (us-east-1) + IAM + EC2 (EIP, volumes e snapshots) em
us-west-2, onde o estudo de casos reais planta recursos (a política IAM só dá ec2:* ali). Fica de
fora, sempre: recursos default (VPC, subnets, SG, route table principal e IGW da VPC default),
chaves KMS gerenciadas pela AWS, usuários/grupos IAM fora dos prefixos `del-`/`mod-`, roles IAM
fora do prefixo `iac-harness-`, dispositivos MFA virtuais atribuídos a um usuário (inclui o MFA do
root), o usuário `default` do ElastiCache e os grupos `default*` do DAX. Os defaults saem já no
inventário: categoria ausente do baseline (tratada como vazia) nunca apaga um default.
Só o que não estava no baseline é apagado. O pacote não importa boto3 no topo: a lógica de
diferença é testável no host.
Lacuna conhecida e aceita: subnets e route tables criadas pelo agente DENTRO da VPC default não
são apagadas (são gratuitas).
Pacote autocontido (só stdlib, imports relativos, nunca `iac_harness`): o executor copia o
diretório inteiro para a toolchain e roda `python3 -m aws_sweep`. `sweep`, `main` e
`_deleters` ficam aqui para que `inventory`/`_deleters` sejam substituíveis no pacote.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from . import databases, ec2, iam, other, storage
from .common import (
    IAM_PREFIXES,
    KMS_PENDING_DAYS,
    ORDER,
    ROLE_PREFIX,
    SECOND_REGION,
    SETTLE_SLEEP_S,
    SETTLE_TRIES,
    WAIT_TIMEOUT_S,
    plan_deletions,
)
from .inventory import inventory

__all__ = [
    "IAM_PREFIXES",
    "KMS_PENDING_DAYS",
    "ORDER",
    "ROLE_PREFIX",
    "SECOND_REGION",
    "SETTLE_SLEEP_S",
    "SETTLE_TRIES",
    "WAIT_TIMEOUT_S",
    "_deleters",
    "inventory",
    "main",
    "plan_deletions",
    "sweep",
    "time",
]


def _deleters(session: Any) -> dict[str, Any]:
    """Categoria → função que apaga um item (pode esperar; erros sobem para `sweep`).

    Junta os registros de cada grupo de serviço, na ordem `ORDER`.
    """
    registry: dict[str, Any] = {}
    for group in (ec2, databases, other, storage, iam):
        registry.update(group.deleters(session))
    return {category: registry[category] for category in ORDER}


def sweep(session: Any, baseline: dict[str, list[str]]) -> dict[str, Any]:
    """Apaga o que não está no baseline e relê o inventário até estabilizar.

    Itens que falharam (ex.: recurso ainda em "creating") são tentados de novo a cada
    releitura; os que já foram apagados só aguardam sumir do inventário.
    """
    deleters = _deleters(session)
    deleted: dict[str, list[str]] = {}
    failed: dict[str, dict[str, str]] = {}

    def delete_all(plan: dict[str, list[str]]) -> None:
        for category, items in plan.items():
            for item in items:
                if item in deleted.get(category, []):
                    continue
                try:
                    deleters[category](item)
                    deleted.setdefault(category, []).append(item)
                    if item in failed.get(category, {}):
                        del failed[category][item]
                        if not failed[category]:
                            del failed[category]
                except Exception as exc:  # noqa: BLE001 (registra e segue; o restante é conferido)
                    failed.setdefault(category, {})[item] = f"{type(exc).__name__}: {exc}"[:500]

    delete_all(plan_deletions(inventory(session), baseline))
    remaining: dict[str, list[str]] = {}
    for attempt in range(SETTLE_TRIES):
        remaining = plan_deletions(inventory(session), baseline)
        if not remaining or attempt == SETTLE_TRIES - 1:
            break
        time.sleep(SETTLE_SLEEP_S)
        delete_all(remaining)
    return {"deleted": deleted, "failed": failed, "remaining": remaining}


def main(argv: list[str]) -> int:
    import boto3

    session = boto3.Session(region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    if argv[:1] == ["inventory"]:
        print(json.dumps(inventory(session)))
        return 0
    if argv[:1] == ["sweep"] and len(argv) == 2:
        with open(argv[1]) as fh:
            baseline = json.load(fh)
        print(json.dumps(sweep(session, baseline)))
        return 0
    print("uso: python3 -m aws_sweep inventory | sweep <baseline.json>", file=sys.stderr)
    return 2
