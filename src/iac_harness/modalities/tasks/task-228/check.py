"""Algum parameter group do DAX na região, fora dos "default*" que a AWS mantém.

O enunciado não fixa nome nem parâmetros: vale qualquer grupo criado.
"""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    dax = boto3.client("dax", region_name=region)
    names, token = [], None
    while True:
        page = dax.describe_parameter_groups(**({"NextToken": token} if token else {}))
        names += [g["ParameterGroupName"] for g in page.get("ParameterGroups", [])]
        token = page.get("NextToken")
        if not token:
            break
    created = [n for n in names if not n.startswith("default")]
    return bool(created), {"parameter_groups": names, "created": created}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
