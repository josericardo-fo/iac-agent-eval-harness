"""Limpeza (ANSI) e truncagem do texto de erro devolvido ao agente."""

from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 2000
TRUNCATION_MARK = "\n[... truncated ...]"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)
