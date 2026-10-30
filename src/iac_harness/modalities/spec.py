"""Modalidades de execução: arquivo esperado do artefato e comandos que o executam."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_TF_FLAGS = ("-input=false", "-no-color")


@dataclass(frozen=True)
class Modality:
    name: str
    filename: str  # arquivo do artefato no workspace
    command: tuple[tuple[str, ...], ...]  # comandos em sequência; para no primeiro que falhar

    @property
    def reference(self) -> str:
        """Nome do arquivo de referência na pasta da tarefa (reference.tf/.sh/.py)."""
        return "reference" + Path(self.filename).suffix


MODALITIES: dict[str, Modality] = {
    "terraform": Modality(
        "terraform",
        "main.tf",
        (
            ("terraform", "init", "-backend=false", *_TF_FLAGS),
            ("terraform", "apply", "-auto-approve", *_TF_FLAGS),
        ),
    ),
    "cli": Modality("cli", "run.sh", (("bash", "-euo", "pipefail", "run.sh"),)),
    "boto3": Modality("boto3", "main.py", (("python3", "main.py"),)),
}

# Pseudo-modalidade da forma livre (E4): o agente escolhe uma das três a cada tentativa, pelo
# arquivo que entrega. Fora de MODALITIES; sem arquivo nem comando próprios (só nome e grupo).
FREE = Modality("free", "", ())
