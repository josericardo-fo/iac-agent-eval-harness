"""Adapter do Ambig-IaC (E16): JSONL da revisão fixa → tarefas do agente (`ModalityTask`).

O parquet do HF (`znyang/ambig-iac`) vira JSONL uma vez, fora do ambiente do projeto
(`scripts/fetch_ambig_iac.py`, com pyarrow descartável); aqui só se lê o JSONL, conferindo o
sha256. As instâncias não mudam: o pedido ao agente é o `prompt` mais uma linha fixa (adaptação
declarada); o `intent` é o cartão do usuário simulado; o oráculo é o `checks_rego` sobre o plan.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from iac_harness.modalities.loader import ModalityTask
from iac_harness.toolchain import Toolchain
from iac_harness.verifiers.plan_rego import grade_config

DATASET = "ambig-iac"
REVISION = "96429693e7164b024333e6d02f8c6b2d017e4ecb"
PARQUET_SHA256 = "3a8dcf6926c94987ed918246513abd7d1906c39f2c64d609709dff177d348fe7"
JSONL_SHA256 = "d1f5525230979df99e0cbe5aaabae5744cda76d320663f4fd9069fe2b2b853a0"
REGION = "us-east-1"
# Linha fixa, igual em todas as condições (o dataset é de síntese de Terraform).
PROMPT_SUFFIX = "Write the Terraform configuration for this in main.tf in the working directory."
ADAPTATION = (
    f"Enunciado = campo `prompt` do Ambig-IaC (revisão {REVISION[:12]}) mais a linha fixa "
    f"{PROMPT_SUFFIX!r}; instância não alterada."
)
FETCH_HINT = (
    "rode: cd /tmp && uv run --no-project --with pyarrow python -I "
    "<repo>/scripts/fetch_ambig_iac.py <repo>/data"
)


def task_id(row_id: int) -> str:
    return f"ambig-{int(row_id):03d}"


def jsonl_path(data_dir: Path) -> Path:
    return Path(data_dir) / f"ambig_iac_{REVISION}.jsonl"


def row_to_task(row: dict[str, Any]) -> ModalityTask:
    return ModalityTask(
        task_id=task_id(row["id"]),
        category="create",
        origin=f"{DATASET}:{row['id']}",
        region=REGION,
        prompt=f"{row['prompt'].strip()}\n\n{PROMPT_SUFFIX}\n",
        check="",
        references={"terraform": row["main_tf"]},
        missing_references=("cli", "boto3"),
        missing_reason="o Ambig-IaC só traz referência Terraform",
        user_card=row["intent"],
        rego_policy=row["checks_rego"],
        adaptation=ADAPTATION,
    )


def load_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Linhas do JSONL, na ordem do parquet; ausente ou com sha256 diferente levanta erro."""
    path = jsonl_path(data_dir)
    if not path.is_file():
        raise SystemExit(f"erro: {path} não existe; {FETCH_HINT}")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != JSONL_SHA256:
        raise SystemExit(f"erro: sha256 de {path} é {digest} (esperado {JSONL_SHA256})")
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def load_tasks(data_dir: Path) -> list[ModalityTask]:
    return [row_to_task(r) for r in load_rows(data_dir)]


def gate_task(toolchain: Toolchain, task: ModalityTask, out_dir: Path) -> dict[str, Any]:
    """Gate: a referência passa no oráculo e um `main.tf` vazio reprova (cópias em `out_dir`)."""
    row: dict[str, Any] = {"task_id": task.task_id, "gate_pass": False, "reason": ""}
    assert task.rego_policy is not None
    for name, text in (("reference", task.references["terraform"]), ("empty", "")):
        work = Path(out_dir) / name
        work.mkdir(parents=True)
        (work / "main.tf").write_text(text)
        passed, row[name] = grade_config(toolchain, task.rego_policy, work)
        if name == "reference" and not passed:
            row["reason"] = f"reference: {row[name]['reason']}"
            return row
        if name == "empty" and passed:
            row["reason"] = "empty main.tf passes"
            return row
    row["gate_pass"] = True
    return row


def provenance() -> dict[str, Any]:
    """Procedência gravada no run_config de runs com o Ambig-IaC."""
    return {
        "name": DATASET, "revision": REVISION, "parquet_sha256": PARQUET_SHA256,
        "jsonl_sha256": JSONL_SHA256, "prompt_suffix": PROMPT_SUFFIX, "adaptation": ADAPTATION,
    }  # fmt: skip
