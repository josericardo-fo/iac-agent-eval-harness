"""Algum dispositivo MFA virtual do IAM existe na conta (fora o MFA do root).

O enunciado não fixa nome nem usuário: vale qualquer dispositivo, atribuído ou não. O do root
(se a conta tiver) fica de fora para o baseline (conta limpa) continuar reprovando.
"""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    iam = boto3.client("iam", region_name=region)
    devices = {}
    for page in iam.get_paginator("list_virtual_mfa_devices").paginate(AssignmentStatus="Any"):
        for d in page.get("VirtualMFADevices", []):
            devices[d["SerialNumber"]] = d.get("User", {}).get("Arn")
    created = [s for s, user in devices.items() if not (user or "").endswith(":root")]
    return bool(created), {"devices": devices, "created": created}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
