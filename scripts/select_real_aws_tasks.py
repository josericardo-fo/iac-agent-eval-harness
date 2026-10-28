"""Sorteio fixo das 7 tarefas de criação que só rodam na AWS real (F2.3) e substituições do gate.

Critério documentado em docs/selecao-tarefas-modalidades.md ("Tarefas só na AWS real").
Determinístico: rodar de novo gera o mesmo src/iac_harness/modalities/selection_real_aws.json.

Uso: uv run python scripts/select_real_aws_tasks.py
"""

import csv
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "iac_eval_v2_defefc108d437f5659f40be66fc863833e9c96fb.csv"
PREVALIDATION_PATH = ROOT / "runs" / "prevalidation-merged" / "prevalidation.jsonl"
OUTPUT_PATH = ROOT / "src" / "iac_harness" / "modalities" / "selection_real_aws.json"

SEED = 20261028
# Estrato -> cota; difficulty >= 3 cai no estrato "3". A ordem do dict é a ordem do sorteio.
STRATA = {"1": 3, "2": 3, "3": 2}
REGION = "us-east-1"

# Tipos (sem o prefixo aws_) gratuitos enquanto parados, ou de custo desprezível.
FREE_TYPES = {
    "kinesis_video_stream", "kinesis_analytics_application", "cloudwatch_composite_alarm",
    "cloudwatch_metric_alarm", "dynamodb_contributor_insights", "dynamodb_table",
    "dynamodb_table_replica", "dax_parameter_group", "dax_subnet_group",
    "sagemaker_human_task_ui", "elasticache_user", "elasticache_user_group",
    "elasticache_user_group_association", "iam_virtual_mfa_device", "glacier_vault",
    "glacier_vault_lock", "sns_topic", "egress_only_internet_gateway", "vpc", "subnet",
    "iam_role", "iam_policy", "iam_role_policy_attachment", "kms_key",
}  # fmt: skip
# Tipos cobrados por hora: só entram na classe micro/nano.
HOURLY_TYPES = {"db_instance", "lightsail_database"}
CHEAP_SIZES = {
    "db.t3.micro", "db.t4g.micro", "micro_1_0", "micro_2_0", "nano_2_0", "nano_3_0", "micro_3_0",
}  # fmt: skip
# Tipos auxiliares que não definem o serviço da tarefa.
AUXILIARY_TYPES = {
    "vpc", "subnet", "iam_role", "iam_policy", "iam_role_policy_attachment", "kms_key",
    "sns_topic", "cloudwatch_metric_alarm",
}  # fmt: skip

RULES = {
    "a": (
        "Pool: linhas de runs/prevalidation-merged com harness_valid verdadeiro e "
        "sandbox_supported falso (não rodam no Floci)."
    ),
    "b": "Excluir enunciados (sem o Intent) que citam 'terraform' ou 'hcl'.",
    "c": 'Excluir reference_output com data "archive_file", aws_lambda_function ou filename =.',
    "d": "Excluir reference_output com algum resource fora dos tipos gratuitos ou por hora.",
    "e": "Excluir tipos por hora (RDS, Lightsail DB) fora das classes micro/nano.",
    "f": "Excluir tarefas que citam outra região (DynamoDB global tables não são excluídas aqui).",
    "g": (
        "Sorteio: só tarefas sem região ou com us-east-1; serviço = primeiro tipo (ordem "
        "alfabética) fora dos auxiliares; no máximo 1 por serviço entre as sorteadas."
    ),
    "h": "Estratos por difficulty: 1 -> 3, 2 -> 3, >=3 -> 2.",
    "i": (
        "Por estrato, na ordem acima: candidatos ordenados, "
        "random.Random(f'{SEED}-{estrato}').shuffle, pega os de serviço ainda não usado; "
        "o resto vira fila de substitutos."
    ),
}

# Reprovadas no gate da AWS real (motivo registrado). O sorteio não muda: o substituto é a
# próxima da fila do mesmo estrato cujo serviço ainda não está em uso (contando as mantidas).
FAILED_GATE = {
    "aws/task-183": (
        "gate 28/10/2026 (runs/20261028T012801-modality-prevalidate): CreateHumanTaskUi -> "
        "ValidationException: Amazon Augmented AI is no longer accepting new customers."
    ),
    "aws/task-388": (
        "gate 28/10/2026 (runs/20261028T012801-modality-prevalidate): CreateVault -> "
        "NoLongerSupportedException: This API is no longer supported for new accounts."
    ),
}

RESOURCE_RE = re.compile(r'resource\s+"(aws_\w+)"')
SIZE_RE = re.compile(r'(instance_class|bundle_id|node_type|instance_type)\s*=\s*"([^"]+)"')
REGION_RE = re.compile(
    r"(?:us|eu|ap|sa|ca)-(?:east|west|north|south|central|northeast|southeast)-\d"
)
TOOL_MENTION_RE = re.compile(r"terraform|hcl", re.IGNORECASE)
ZIP_RE = re.compile(r'data\s+"archive_file"|aws_lambda_function|filename\s*=')


def load_csv() -> dict[str, dict[str, str]]:
    csv.field_size_limit(2**31 - 1)
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return {row["task_id"]: row for row in csv.DictReader(f)}


def load_unsupported() -> list[str]:
    ids = []
    with PREVALIDATION_PATH.open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["harness_valid"] and not record["sandbox_supported"]:
                ids.append(record["task_id"])
    return sorted(ids)


def classify(row: dict[str, str]) -> tuple[str | None, dict]:
    """Regra que exclui a tarefa (ou None) e os atributos usados no sorteio."""
    reference = row["reference_output"]
    statement = row["prompt"].split("## Intent")[0]
    types = {t.removeprefix("aws_") for t in RESOURCE_RE.findall(reference)}
    sizes = {v for _, v in SIZE_RE.findall(reference)}
    regions = set(REGION_RE.findall(statement + reference))
    if TOOL_MENTION_RE.search(statement):
        return "b", {}
    if ZIP_RE.search(reference):
        return "c", {}
    if types - FREE_TYPES - HOURLY_TYPES:
        return "d", {}
    if types & HOURLY_TYPES and not sizes <= CHEAP_SIZES:
        return "e", {}
    if regions - {REGION} and not types & {"dynamodb_table", "dynamodb_table_replica"}:
        return "f", {}
    return None, {
        "service": sorted(types - AUXILIARY_TYPES)[0],
        "sizes": sorted(sizes),
        "regions": sorted(regions),
    }


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stratum_of(difficulty: str) -> str:
    return str(min(int(difficulty), 3))


def main() -> None:
    rows = load_csv()
    unsupported = load_unsupported()

    excluded: dict[str, list[str]] = {"b": [], "c": [], "d": [], "e": [], "f": []}
    pool: dict[str, dict] = {}
    for task_id in unsupported:
        rule, attrs = classify(rows[task_id])
        if rule:
            excluded[rule].append(task_id)
        else:
            pool[task_id] = attrs
    candidates = sorted(t for t, a in pool.items() if set(a["regions"]) <= {REGION})

    used_services: Counter[str] = Counter()
    selected: dict[str, list[str]] = {}
    substitutes: dict[str, list[str]] = {}
    for stratum, quota in STRATA.items():
        ids = sorted(t for t in candidates if stratum_of(rows[t]["difficulty"]) == stratum)
        random.Random(f"{SEED}-{stratum}").shuffle(ids)
        picked, queue = [], []
        for task_id in ids:
            service = pool[task_id]["service"]
            if len(picked) < quota and not used_services[service]:
                picked.append(task_id)
                used_services[service] += 1
            else:
                queue.append(task_id)
        selected[stratum] = picked
        substitutes[stratum] = queue

    # Substituições: o serviço da reprovada sai de uso; o substituto entra no lugar dela.
    replacements = []
    for stratum, ids in selected.items():
        for failed in [t for t in ids if t in FAILED_GATE]:
            used_services[pool[failed]["service"]] -= 1
            sub = next(t for t in substitutes[stratum] if not used_services[pool[t]["service"]])
            used_services[pool[sub]["service"]] += 1
            replacements.append(
                {"stratum": stratum, "out": failed, "in": sub, "reason": FAILED_GATE[failed]}
            )

    def describe(task_id: str) -> str:
        return f"{task_id:<14} {rows[task_id]['difficulty']:<5} {pool[task_id]['service']}"

    print(f"Não rodam no Floci: {len(unsupported)}; pool após b-f: {len(pool)}")
    for rule, ids in excluded.items():
        print(f"Excluídas pela regra {rule}: {len(ids)}")
    print(f"Candidatas (só {REGION}): {len(candidates)}")
    print()
    print(f"{'estrato':<8} {'task_id':<14} {'dif':<5} serviço")
    for stratum, ids in selected.items():
        if len(ids) < STRATA[stratum]:
            print(f"(estrato {stratum}: {len(ids)} de {STRATA[stratum]}, faltam serviços novos)")
        for task_id in ids:
            print(f"{stratum:<8} {describe(task_id)}")
    for stratum, ids in substitutes.items():
        print(f"\nSubstitutos do estrato {stratum} (em ordem):")
        for task_id in ids:
            print(f"  {describe(task_id)}")
    for r in replacements:
        print(f"\nEstrato {r['stratum']}: {r['out']} -> {r['in']} ({r['reason']})")

    output = {
        "seed": SEED,
        # Entradas fora do git (data/ e runs/): o hash confere se são as mesmas do sorteio.
        "inputs_sha256": {
            str(p.relative_to(ROOT)): sha256_of(p) for p in (CSV_PATH, PREVALIDATION_PATH)
        },
        "rules": RULES,
        "excluded": excluded,
        "pool": pool,
        "selected": selected,
        "substitutes": substitutes,
        "replacements": replacements,
    }
    OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nGravado em {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
