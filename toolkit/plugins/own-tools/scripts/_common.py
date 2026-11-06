"""Utilidades comuns dos scripts: sessão AWS, região, endpoint e saída JSON.

O boto3 é importado só quando há chamada à AWS, para que `--help` funcione sem ele.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable


def region() -> str:
    # AWS CLI e Terraform leem AWS_REGION; o boto3 só lê AWS_DEFAULT_REGION.
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def endpoint() -> str | None:
    """Endpoint customizado (emulador, VPC endpoint); None = endpoint público da AWS."""
    return os.environ.get("AWS_ENDPOINT_URL") or None


def session(region_name: str | None = None):
    import boto3  # noqa: PLC0415 (import tardio: --help sem boto3)

    # O botocore já respeita AWS_ENDPOINT_URL e a cadeia padrão de credenciais.
    return boto3.Session(region_name=region_name or region())


def emit(obj: object, code: int = 0) -> None:
    print(json.dumps(obj, indent=2, default=str))
    sys.exit(code)


def run(main: Callable[[], int]) -> None:
    """Roda `main`; qualquer exceção vira JSON `{"error": ...}` com saída 1."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 (o script nunca termina com traceback cru)
        emit({"error": f"{type(exc).__name__}: {exc}"}, 1)
