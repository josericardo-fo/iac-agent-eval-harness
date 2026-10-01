"""Normaliza a primeira falha em texto para o agente e classifica (taxonomia de Nekrasov)."""

from __future__ import annotations

import re

from iac_harness.types import CheckResult, CheckStatus, Feedback, Stage

DEFAULT_MAX_CHARS = 2000
TRUNCATION_MARK = "\n[... truncated ...]"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# Ordem importa: a primeira classe que casar vence.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Syntax",
        re.compile(
            r"syntax|unclosed|unterminated|invalid character|argument or block definition"
            r"|unsupported block type|invalid expression",
            re.IGNORECASE,
        ),
    ),
    (
        "Schema",
        re.compile(
            r"unsupported argument|missing required argument|unsupported attribute"
            r"|reference to undeclared|invalid reference|incorrect attribute value type"
            r"|inappropriate value|invalid value for|duplicate (resource|provider)"
            r"|invalid (resource|data source) type",
            re.IGNORECASE,
        ),
    ),
    (
        "Runtime",
        re.compile(
            r"invalidparameter|accessdenied|alreadyexists|notfound|validationerror"
            r"|error (creating|reading|updating|deleting)|status code|operation error",
            re.IGNORECASE,
        ),
    ),
)


def classify(result: CheckResult) -> str:
    """Classe de erro: Syntax, Schema, Runtime, Intent ou Unknown."""
    if result.stage is Stage.OPA:
        return "Intent"
    text = f"{result.message}\n{result.raw}"
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    return "Runtime" if result.stage is Stage.SANDBOX else "Unknown"


def build_feedback(result: CheckResult, max_chars: int = DEFAULT_MAX_CHARS) -> Feedback | None:
    """Feedback para o agente; só falhas reais viram feedback (UNSUPPORTED/ERROR não)."""
    if result.status is not CheckStatus.FAIL:
        return None
    message = strip_ansi(result.message).strip()
    if len(message) > max_chars:
        message = message[:max_chars].rstrip() + TRUNCATION_MARK
    return Feedback(stage=result.stage, error_class=classify(result), message=message)
