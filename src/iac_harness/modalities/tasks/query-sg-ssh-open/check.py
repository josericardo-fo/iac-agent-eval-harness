"""answer.json lista exatamente os SGs com TCP 22 aberto para 0.0.0.0/0 (gabarito fixo)."""

import json
import sys

KEY = "security_groups"
EXPECTED = {"query-bastion-sg", "query-legacy-sg"}


def main(region: str) -> tuple[bool, dict]:
    try:
        with open("answer.json") as fh:
            answer = json.load(fh)
    except (OSError, ValueError) as exc:
        return False, {"error": f"answer.json ilegível: {exc}", "expected": sorted(EXPECTED)}
    got = answer.get(KEY) if isinstance(answer, dict) else None
    if not isinstance(got, list) or not all(isinstance(x, str) for x in got):
        return False, {"answer": answer, "expected": sorted(EXPECTED)}
    return set(got) == EXPECTED, {"got": sorted(set(got)), "expected": sorted(EXPECTED)}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
