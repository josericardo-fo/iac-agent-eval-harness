"""Adapter do Multi-IaC-Eval (AmazonScience): triplas Terraform (initial, utterance, expected).

Lê o test.csv já baixado (dado não confiável: só parsing, nada é executado aqui) e mantém as
triplas cujos providers cabem no mirror da toolchain e que não usam `module`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

# Revisão do dataset no HF (AmazonScience/Multi-IaC-Eval); o test.csv dela tem git blob
# 0905da67dbeecf53ecf943e5904f37a949ddf519.
DEFAULT_REVISION = "e3dd91b42c13f20a86fb23a2cf8a5d84126f6fc3"
MIRROR_PROVIDERS = frozenset({"aws", "archive", "random", "awscc"})

_RESOURCE_PROVIDER = re.compile(r'^\s*(?:resource|data)\s+"([a-z0-9]+)_[a-z0-9_]+"', re.MULTILINE)
_PROVIDER_BLOCK = re.compile(r'^\s*provider\s+"([a-z0-9_]+)"', re.MULTILINE)
_MODULE_BLOCK = re.compile(r'^\s*module\s+"', re.MULTILINE)


@dataclass(frozen=True)
class Triple:
    triple_id: str  # source + índice da linha no CSV
    utterance: str
    initial: str
    expected: str
    source_repo: str


def providers_of(text: str) -> set[str]:
    """Providers usados (prefixo de resource/data e blocos `provider`)."""
    return set(_RESOURCE_PROVIDER.findall(text)) | set(_PROVIDER_BLOCK.findall(text))


def is_compatible(initial: str, expected: str) -> bool:
    """initial ∪ expected usa só providers do mirror e nenhum `module`."""
    providers = providers_of(initial) | providers_of(expected)
    uses_module = bool(_MODULE_BLOCK.search(initial) or _MODULE_BLOCK.search(expected))
    return bool(providers) and providers <= MIRROR_PROVIDERS and not uses_module


class MultiIacEvalAdapter:
    def __init__(self, csv_path: Path, revision: str = DEFAULT_REVISION) -> None:
        self.csv_path = Path(csv_path)
        self.revision = revision

    @property
    def version(self) -> str:
        return self.revision

    def sha256(self) -> str:
        return hashlib.sha256(self.csv_path.read_bytes()).hexdigest()

    def load(self) -> list[Triple]:
        """Triplas Terraform únicas por (initial, utterance, expected) e compatíveis."""
        text = self.csv_path.read_text(encoding="utf-8")
        csv.field_size_limit(2**31 - 1)
        seen: set[tuple[str, str, str]] = set()
        triples: list[Triple] = []
        for index, row in enumerate(csv.DictReader(io.StringIO(text, newline=""))):
            if row["type"] != "terraform":
                continue
            key = (row["initial"], row["utterance"], row["expected"])
            if key in seen:
                continue
            seen.add(key)
            if not is_compatible(row["initial"], row["expected"]):
                continue
            triples.append(
                Triple(
                    triple_id=f"{row['source']}#{index}",
                    utterance=row["utterance"],
                    initial=row["initial"],
                    expected=row["expected"],
                    source_repo=row["source_repo"],
                )
            )
        return triples
