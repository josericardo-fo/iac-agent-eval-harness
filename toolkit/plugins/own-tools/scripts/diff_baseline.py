#!/usr/bin/env python3
"""Compara um inventário atual com um baseline salvo: recursos que apareceram e que sumiram."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _common import emit, run


def _key(r: dict) -> tuple:
    return (r["type"], r.get("region"), r["id"])


def diff(baseline: dict, current: dict) -> dict:
    """Recurso = (tipo, região, id); mudança só de tag não conta. Erros tornam o diff parcial."""
    before = {_key(r): r for r in baseline["resources"]}
    after = {_key(r): r for r in current["resources"]}
    errors = [{"in": "baseline", **e} for e in baseline.get("errors", [])] + [
        {"in": "current", **e} for e in current.get("errors", [])
    ]
    return {
        "added": [after[k] for k in sorted(after.keys() - before.keys(), key=str)],
        "removed": [before[k] for k in sorted(before.keys() - after.keys(), key=str)],
        "errors": errors,
    }


def _current_inventory(baseline: dict) -> dict:
    """Roda o inventory.py ao lado com as mesmas regiões e filtro de tag do baseline."""
    cmd = [sys.executable, str(Path(__file__).with_name("inventory.py"))]
    if baseline.get("regions"):
        cmd += ["--regions", *baseline["regions"]]
    for k, v in (baseline.get("tag_filter") or {}).items():
        cmd += ["--tag", f"{k}={v}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"inventory.py falhou: {proc.stdout.strip() or proc.stderr.strip()}")
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show resources added and removed since a baseline saved by "
        "'inventory.py --save' (JSON). Without --current, takes a fresh inventory with the "
        "baseline's regions and tag filter."
    )
    parser.add_argument("baseline", type=Path, help="baseline JSON from inventory.py --save")
    parser.add_argument("--current", type=Path, help="current inventory JSON (default: take one)")
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text())
    current = json.loads(args.current.read_text()) if args.current else _current_inventory(baseline)
    emit(diff(baseline, current))
    return 0


if __name__ == "__main__":
    run(main)
