"""Oráculo do Ambig-IaC (E16): `terraform plan` dos arquivos finais do agente + OPA no `data`.

Os arquivos do /work do agente são copiados para um diretório limpo (sem estado, lock, `.terraform`
nem o override do agente), com o override do Floci do harness; o plan usa o `PlanVerifier` do
A/B/C. A regra de aprovação é a do avaliador do IaC-Eval (de onde vêm os `checks_rego`): `opa eval
data` e reprova se alguma folha do documento for `false` (aqui só booleanos; o IaC-Eval, em Python,
também reprovaria um `0`). Os pacotes variam por tarefa e não há regra `main`, por isso não se usa
o `OpaVerifier` (`data.terraform.policy.main`). Políticas sem `import rego.v1` são Rego v0: com o
OPA 1.x da toolchain, rodam com `--v0-compatible` (o IaC-Eval usava OPA 0.x).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain, remove_terraform_dir
from iac_harness.types import CheckStatus, TaskOracle
from iac_harness.verifiers.terraform import PLAN_JSON, PlanVerifier
from iac_harness.workspace import OVERRIDE_FILENAME, Workspace

# Mesmo nome de agentic.runner.AGENT_OVERRIDE_FILENAME (um teste confere; importar daria ciclo).
AGENT_OVERRIDE_FILENAME = "zz_provider_override.tf"
_SKIPPED_DIRS = {".terraform"}
_SKIPPED_FILES = {".terraform.lock.hcl", AGENT_OVERRIDE_FILENAME, OVERRIDE_FILENAME, "tfplan",
                  PLAN_JSON, "answer.json"}  # fmt: skip
MAX_ERROR_CHARS = 1500
# Valores de exemplo das variáveis obrigatórias (só no plan do oráculo; o agente não os vê).
PLACEHOLDERS_FILENAME = "zz_harness_placeholders.auto.tfvars.json"
_PLACEHOLDER_BY_TYPE: dict[str, Any] = {
    "number": 1, "bool": False, "list": [], "set": [], "tuple": [], "map": {}, "object": {},
}  # fmt: skip
_PLACEHOLDER_DEFAULT = "example"  # string, any ou sem tipo
_VARIABLE_RE = re.compile(r'(?m)^[ \t]*variable[ \t]+"([^"]+)"[ \t]*\{')
_TYPE_RE = re.compile(r"(?m)^[ \t]*type[ \t]*=[ \t]*(\w+)")
_DEFAULT_RE = re.compile(r"(?m)^[ \t]*default[ \t]*=")


def _skipped(name: str) -> bool:
    return name in _SKIPPED_FILES or name.endswith(".tfstate") or ".tfstate." in name


def _config_files(work: Path) -> list[Path]:
    """Arquivos a copiar (relativos ao /work), sem links e sem estado/override/.terraform."""
    found = []
    for dirpath, dirnames, filenames in os.walk(work):
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS
                       and not (Path(dirpath) / d).is_symlink()]  # fmt: skip
        for name in filenames:
            path = Path(dirpath) / name
            if not path.is_symlink() and not _skipped(name):
                found.append(path.relative_to(work))
    return sorted(found)


def opa_args(rego: str) -> list[str]:
    """`opa eval` do pacote inteiro (como o IaC-Eval), v0 salvo com `import rego.v1`."""
    compat = [] if "import rego.v1" in rego else ["--v0-compatible"]
    return ["opa", "eval", *compat, "-d", "policy.rego", "-i", PLAN_JSON, "data",
            "--format", "json"]  # fmt: skip


def false_rules(value: Any, prefix: str = "") -> list[str]:
    """Caminhos (a.b.c) das folhas `false` do documento `data` avaliado."""
    if value is False:
        return [prefix]
    if isinstance(value, dict):
        return [
            p for k, v in value.items() for p in false_rules(v, f"{prefix}.{k}" if prefix else k)
        ]
    if isinstance(value, list):
        return [p for v in value for p in false_rules(v, prefix)]
    return []


def bool_leaves(value: Any, prefix: str = "") -> list[tuple[str, bool]]:
    """(caminho, valor) das folhas booleanas do documento `data` (caminhos de false_rules)."""
    if isinstance(value, bool):
        return [(prefix, value)]
    if isinstance(value, dict):
        return [
            leaf
            for k, v in value.items()
            for leaf in bool_leaves(v, f"{prefix}.{k}" if prefix else k)
        ]
    if isinstance(value, list):
        return [leaf for v in value for leaf in bool_leaves(v, prefix)]
    return []


def rules_fraction(leaves: list[tuple[str, bool]], rules: tuple[str, ...]) -> float:
    """Fração das `rules` (caminhos) verdadeiras: há folha `true` no caminho e nenhuma `false`.

    Regra ausente do documento (indefinida no Rego) conta como falsa.
    """
    state: dict[str, bool] = {}
    for path, ok in leaves:
        state[path] = state.get(path, True) and ok
    return sum(state.get(r, False) for r in rules) / len(rules)


def plan_resource_types(plan: dict[str, Any]) -> list[str]:
    """Tipos dos recursos gerenciados do módulo raiz (`configuration.root_module.resources`)."""
    resources = (plan.get("configuration") or {}).get("root_module", {}).get("resources") or []
    return [r["type"] for r in resources if r.get("mode", "managed") == "managed" and "type" in r]


def type_f1(actual: list[str], reference: list[str]) -> float:
    """F1 entre os multiconjuntos de tipos de recurso (aproximação do Structure Score)."""
    if not actual and not reference:
        return 1.0
    overlap = sum((Counter(actual) & Counter(reference)).values())
    if not overlap:
        return 0.0
    precision, recall = overlap / len(actual), overlap / len(reference)
    return 2 * precision * recall / (precision + recall)


def _skip_string(text: str, i: int) -> int:
    """Índice logo após a string que abre em `text[i]` (aspas), respeitando `\\`."""
    i += 1
    while i < len(text) and text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _top_level(text: str, start: int) -> tuple[str, int]:
    """Texto no nível 0 do bloco aberto antes de `start` (sem aninhados, strings e comentários)
    e o índice depois do `}` que o fecha."""
    out: list[str] = []
    depth, i = 1, start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
            out.append('""' if depth == 1 else "")
            continue
        if ch == "#" or text.startswith("//", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return "".join(out), i + 1
        elif depth == 1:
            out.append(ch)
        i += 1
    return "".join(out), len(text)


def required_variable_placeholders(tf_texts: list[str]) -> dict[str, Any]:
    """Valor de exemplo (pelo tipo declarado) de cada `variable` sem `default` nos .tf dados."""
    values: dict[str, Any] = {}
    for text in tf_texts:
        for m in _VARIABLE_RE.finditer(text):
            body, _ = _top_level(text, m.end())
            if _DEFAULT_RE.search(body):
                continue
            kind = _TYPE_RE.search(body)
            values[m.group(1)] = _PLACEHOLDER_BY_TYPE.get(
                kind.group(1) if kind else "", _PLACEHOLDER_DEFAULT
            )
    return values


_ZERO_SCORES = {
    "rules_true": 0, "rules_total": 0, "rego_frac": 0.0, "rego_frac_all": 0.0, "type_f1": 0.0,
}  # fmt: skip


def grade_config(
    toolchain: Toolchain,
    rego: str,
    work: Path | None,
    reference_types: tuple[str, ...] | None = None,
    rego_rules: tuple[str, ...] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """(passou, detalhes); `reason`: no_tf | plan_failed | rego_pass | rego_fail | oracle_error.

    Além do passa/reprova, os detalhes trazem as notas parciais: `rego_frac` (fração das regras
    discriminantes `rego_rules` verdadeiras; sem `rego_rules`, cai para `rego_frac_all`, a fração
    de todas as folhas booleanas verdadeiras) e `type_f1` (tipos de recurso × `reference_types`;
    None sem referência). Todas são 0.0 sem `.tf`, com plan falho ou erro do oráculo.
    """
    files = _config_files(work) if work is not None and work.is_dir() else []
    if not any(f.suffix == ".tf" and len(f.parts) == 1 for f in files):
        return False, {"reason": "no_tf", **_ZERO_SCORES}
    if toolchain.target != "emulator":
        return False, {"reason": "oracle_error", "error": "oráculo Ambig-IaC só no emulador",
                       **_ZERO_SCORES}  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="iac-ambig-", ignore_cleanup_errors=True) as tmp:
        config, opa_dir = Path(tmp) / "config", Path(tmp) / "opa"
        try:
            return _grade(
                toolchain, rego, work, files, config, opa_dir, reference_types, rego_rules
            )
        finally:
            remove_terraform_dir(config, toolchain)


def _grade(
    toolchain: Toolchain, rego: str, work: Path, files: list[Path], config: Path, opa_dir: Path,
    reference_types: tuple[str, ...] | None = None,
    rego_rules: tuple[str, ...] | None = None,
) -> tuple[bool, dict[str, Any]]:  # fmt: skip
    for rel in files:
        (config / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / rel, config / rel)
    tf = {f.name: (config / f).read_text(errors="replace") for f in files if len(f.parts) == 1
          and f.suffix == ".tf"}  # fmt: skip
    override = FlociExecutor(toolchain, endpoint=toolchain.endpoint).override_for(tf)
    (config / OVERRIDE_FILENAME).write_text(override)
    placeholders = required_variable_placeholders(list(tf.values()))
    if placeholders:  # só no plan do oráculo; o /work do agente não muda
        (config / PLACEHOLDERS_FILENAME).write_text(json.dumps(placeholders))
    plan = PlanVerifier(toolchain).check(Workspace(config, ""), TaskOracle({}, rego))
    if plan.status is not CheckStatus.PASS:
        reason = "plan_failed" if plan.status is CheckStatus.FAIL else "oracle_error"
        return False, {"reason": reason, "error": plan.message[:MAX_ERROR_CHARS], **_ZERO_SCORES}
    opa_dir.mkdir()
    (opa_dir / "policy.rego").write_text(rego)
    shutil.copyfile(config / PLAN_JSON, opa_dir / PLAN_JSON)
    try:
        proc = toolchain.run(opa_args(rego), opa_dir)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[:500])
        results = json.loads(proc.stdout).get("result") or []
        value = results[0]["expressions"][0]["value"] if results else {}
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError, KeyError) as exc:
        return False, {"reason": "oracle_error", "error": f"opa: {exc}"[:MAX_ERROR_CHARS],
                       **_ZERO_SCORES}  # fmt: skip
    failed = false_rules(value)
    passed = not failed
    leaves = bool_leaves(value)
    true_n = sum(ok for _, ok in leaves)
    # Documento sem folha booleana: não há regra a medir, então a nota é a do passa/reprova.
    frac_all = true_n / len(leaves) if leaves else float(passed)
    frac = rules_fraction(leaves, rego_rules) if rego_rules else frac_all
    f1 = None
    if reference_types is not None:
        actual = plan_resource_types(json.loads((config / PLAN_JSON).read_text()))
        f1 = type_f1(actual, list(reference_types))
    return passed, {
        "reason": "rego_fail" if failed else "rego_pass", "failed_rules": failed,
        "rules_true": true_n, "rules_total": len(leaves), "rego_frac": frac,
        "rego_frac_all": frac_all, "type_f1": f1,
    }  # fmt: skip
