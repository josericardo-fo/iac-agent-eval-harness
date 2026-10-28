"""Adaptador do check do aws-bench: roda upstream/check.py (cópia literal) sem alterá-lo.

Define as variáveis do [verifier.env] com os nossos nomes, injeta um `rewardkit` falso que
registra os critérios e troca no texto só o caminho /logs/agent/agent-output.json por
answer.json. pass só se todos os critérios passarem; details = {descrição: resultado}.
"""

import json
import os
import sys
import types
from pathlib import Path

ENV = {
    "AWS_REGION": "us-east-1",
    "SYNC_SOURCE_BUCKET": "awsb-sync-source-nv0qp1",
    "DESTINATION_BUCKET": "awsb-sync-destination-nv0qp1",
}


def main() -> tuple[bool, dict]:
    os.environ.update(ENV)
    criteria = []

    def criterion(description, **_):
        def register(fn):
            criteria.append((description, fn))
            return fn

        return register

    rewardkit = types.ModuleType("rewardkit")
    rewardkit.criterion = criterion
    sys.modules["rewardkit"] = rewardkit
    source = Path("upstream/check.py").read_text()
    source = source.replace("/logs/agent/agent-output.json", "answer.json")
    exec(compile(source, "upstream/check.py", "exec"), {"__name__": "upstream_check"})
    details = {}
    for description, fn in criteria:
        try:
            details[description] = fn(workspace=Path("."))
        except Exception as exc:  # critério que quebra reprova, com o erro nos detalhes
            details[description] = f"{type(exc).__name__}: {exc}"
    return bool(criteria) and all(v is True for v in details.values()), details


if __name__ == "__main__":
    try:
        passed, details = main()
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
