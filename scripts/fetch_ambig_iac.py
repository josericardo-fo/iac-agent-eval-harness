"""Baixa o Ambig-IaC (HF `znyang/ambig-iac`) na revisão fixa e converte o parquet em JSONL.

Ferramenta descartável, FORA do ambiente do projeto (o pyarrow não é dependência do harness):

    cd /tmp && uv run --no-project --with pyarrow python -I \\
        <repo>/scripts/fetch_ambig_iac.py <repo>/data

O parquet é tratado como dado não confiável: vai para um diretório temporário novo e vazio, tem
o sha256 conferido contra o do LFS na revisão fixa e só é lido como tabela (nada dele é executado).
O JSONL (uma linha por tarefa, na ordem do parquet) sai em `<data>/ambig_iac_<revisão>.jsonl`;
o adaptador (`iac_harness.datasets.ambig_iac`) confere o sha256 dele ao carregar.
"""

import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

# Mesmos valores de iac_harness.datasets.ambig_iac (um teste confere).
REVISION = "96429693e7164b024333e6d02f8c6b2d017e4ecb"
PARQUET_SHA256 = "3a8dcf6926c94987ed918246513abd7d1906c39f2c64d609709dff177d348fe7"
URL = (
    "https://huggingface.co/datasets/znyang/ambig-iac/resolve/"
    f"{REVISION}/data/train-00000-of-00001.parquet"
)
FIELDS = ("id", "prompt", "prompt_original", "intent", "main_tf", "checks_rego", "plan_json")


def main(data_dir: Path) -> None:
    import pyarrow.parquet as pq  # só existe no ambiente descartável

    with tempfile.TemporaryDirectory(prefix="ambig-iac-") as tmp:
        parquet = Path(tmp) / "train.parquet"
        with urllib.request.urlopen(URL, timeout=120) as resp:  # noqa: S310 (URL fixa, https)
            parquet.write_bytes(resp.read())
        digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
        if digest != PARQUET_SHA256:
            raise SystemExit(f"sha256 do parquet difere: {digest} (esperado {PARQUET_SHA256})")
        rows = pq.read_table(parquet, columns=list(FIELDS)).to_pylist()
    lines = [json.dumps({k: row[k] for k in FIELDS}, ensure_ascii=False) for row in rows]
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / f"ambig_iac_{REVISION}.jsonl"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} tarefas em {out}")
    print(f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
