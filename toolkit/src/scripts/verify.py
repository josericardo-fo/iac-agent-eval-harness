#!/usr/bin/env python3
"""Repete uma chamada de leitura até as expectativas valerem ou o tempo acabar.

Serve para conferir o estado final com espera da consistência eventual.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable

from _common import emit, run, session

_MISSING = object()
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def method_name(operation: str) -> str:
    """`describe-db-instances`, `DescribeDBInstances` ou `describe_db_instances` → snake_case."""
    if "-" in operation or "_" in operation:
        return operation.replace("-", "_").lower()
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", operation)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def parse_expect(spec: str) -> tuple[str, object]:
    """`PATH=VALUE`; VALUE é lido como JSON quando possível (`5`, `true`, `[]`), senão texto."""
    path, sep, raw = spec.partition("=")
    if not sep or not path:
        raise ValueError(f"--expect inválido (use PATH=VALUE): {spec!r}")
    try:
        return path, json.loads(raw)
    except json.JSONDecodeError:
        return path, raw


def lookup(doc: object, path: str) -> object:
    """Caminho no estilo `A.B[0].C`; devolve `_MISSING` se não existir."""
    cur = doc
    for key, index in _TOKEN.findall(path):
        if key:
            if not isinstance(cur, dict) or key not in cur:
                return _MISSING
            cur = cur[key]
        else:
            i = int(index)
            if not isinstance(cur, list) or i >= len(cur):
                return _MISSING
            cur = cur[i]
    return cur


def _norm(value: object) -> str:
    # A API costuma devolver números como texto ("345600"): compara pela forma textual.
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


def mismatches(doc: object, expects: list[tuple[str, object]]) -> list[dict]:
    out = []
    for path, expected in expects:
        actual = lookup(doc, path)
        if actual is _MISSING:
            out.append({"path": path, "expected": expected, "actual": None})
        elif _norm(actual) != _norm(expected):
            out.append({"path": path, "expected": expected, "actual": actual})
    return out


def verify(
    client,
    call: str,
    params: dict,
    expects: list[tuple[str, object]],
    timeout: float = 60,
    interval: float = 5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    method = getattr(client, method_name(call))  # operação inexistente falha já, sem repetir
    start = clock()
    attempts, last_error, last_mismatches = 0, None, []
    while True:
        attempts += 1
        try:
            last_mismatches = mismatches(method(**params), expects)
            last_error = None
            if not last_mismatches:
                return {"ok": True, "attempts": attempts, "elapsed_s": round(clock() - start, 1)}
        except Exception as exc:  # noqa: BLE001 (erro da leitura conta como "ainda não")
            last_error = f"{type(exc).__name__}: {exc}"
        elapsed = clock() - start
        if elapsed >= timeout:
            return {
                "ok": False,
                "attempts": attempts,
                "elapsed_s": round(elapsed, 1),
                "mismatches": last_mismatches,
                "last_error": last_error,
            }
        sleep(min(interval, timeout - elapsed))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat a read-only AWS call until every expectation holds or the timeout "
        "passes. Exit 0 if it holds, 1 if not (JSON on stdout).",
        epilog="Example: verify.py --service dynamodb --call describe-table "
        '--params \'{"TableName": "orders"}\' --expect Table.TableStatus=ACTIVE',
    )
    parser.add_argument("--service", required=True, help="boto3 service name, e.g. s3, sqs")
    parser.add_argument("--call", required=True, help="read operation, e.g. get-queue-attributes")
    parser.add_argument("--params", default="{}", help="JSON object with the call parameters")
    parser.add_argument(
        "--expect",
        action="append",
        required=True,
        metavar="PATH=VALUE",
        help="expected value at a path like A.B[0].C (repeatable; VALUE parsed as JSON if valid)",
    )
    parser.add_argument("--timeout", type=float, default=60, help="seconds (default 60)")
    parser.add_argument("--interval", type=float, default=5, help="seconds between reads")
    parser.add_argument("--region", help="override AWS_REGION")
    args = parser.parse_args()

    client = session(args.region).client(args.service)
    result = verify(
        client,
        args.call,
        json.loads(args.params),
        [parse_expect(e) for e in args.expect],
        timeout=args.timeout,
        interval=args.interval,
    )
    emit(result, 0 if result["ok"] else 1)
    return 0


if __name__ == "__main__":
    run(main)
