#!/usr/bin/env python3
"""Mostra a conta, a identidade (ARN), a região e o endpoint que as chamadas AWS vão usar."""

from __future__ import annotations

import argparse

from _common import emit, endpoint, region, run, session


def main() -> int:
    argparse.ArgumentParser(
        description="Print the AWS account, caller ARN, region and endpoint in use (JSON)."
    ).parse_args()
    ident = session().client("sts").get_caller_identity()
    emit(
        {
            "account": ident["Account"],
            "arn": ident["Arn"],
            "user_id": ident["UserId"],
            "region": region(),
            "endpoint": endpoint() or "default (public AWS)",
        }
    )
    return 0


if __name__ == "__main__":
    run(main)
