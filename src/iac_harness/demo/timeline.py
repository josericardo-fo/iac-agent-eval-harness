"""Trajetória gravada (`unit_dir`) -> `timeline.json` para o vídeo da demo.

Nós, arestas e ondas vêm do `work/terraform.tfstate`; os passos, do `transcript.jsonl`; os
metadados, do evento `final` em `events.jsonl` (e do veredito do juiz, se existir).
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from iac_harness.agentic.agents import secret_values
from iac_harness.agentic.judge import JUDGE_FILENAME
from iac_harness.agentic.runner import model_slug, trajectory_id
from iac_harness.agentic.stream import TOOLKIT_READ_SCRIPTS, call_ops, parse_stream, redact
from iac_harness.metrics import load_events

CAPTIONS = {
    "read": "leu a conta",
    "verify": "conferiu",
    "write": "escreveu o Terraform",
    "plan": "rodou o plan",
    "apply": "aplicou a mudança",
    "ask": "pediu confirmação",
    "done": "concluiu",
}
SKILL_CAPTION = "consultou a skill"
MAX_QUESTION = 300

# Prefixo do tipo (sem `aws_`) -> grupo; o primeiro que casar vale.
_GROUPS = (
    ("rede", ("vpc", "subnet", "route", "security_group", "internet_gateway", "nat_gateway")),
    ("dados", ("s3", "db_", "dynamodb", "sqs")),
    ("computacao", ("instance", "lambda", "lightsail")),
    ("identidade", ("iam",)),
)
_SCRIPT_RE = re.compile(r"\b(" + "|".join(s[:-3] for s in sorted(TOOLKIT_READ_SCRIPTS)) + r")\.py")


def _group(rtype: str) -> str:
    short = rtype.removeprefix("aws_")
    for group, prefixes in _GROUPS:
        if short.startswith(prefixes):
            return group
    return "outros"


def _graph(state_path: Path) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """Nós (com onda topológica) e arestas dos recursos `managed`; sem state, vazio."""
    if not state_path.exists():
        return [], []
    resources = json.loads(state_path.read_text(encoding="utf-8")).get("resources") or []
    managed = {f"{r['type']}.{r['name']}": r for r in resources if r.get("mode") == "managed"}
    deps = {}
    for rid, res in managed.items():
        raw = ((res.get("instances") or [{}])[0]).get("dependencies") or []
        deps[rid] = sorted({d for d in raw if d in managed and d != rid})

    @cache
    def wave(rid: str) -> int:
        return 1 + max((wave(d) for d in deps[rid]), default=0)

    nodes = [
        {"id": rid, "type": res["type"], "group": _group(res["type"]), "wave": wave(rid)}
        for rid, res in managed.items()
    ]
    edges = [[d, rid] for rid in managed for d in deps[rid]]
    return nodes, edges


def _kind(call: dict[str, Any], ops: list[str], mutated_before: bool) -> tuple[str, str | None]:
    name, data = call.get("name"), call.get("input") or {}
    if name == "Skill":
        return "think", None
    if name == "Bash" and "mutate" in ops:
        return "apply", None  # mutar vale mais que ler (`aws s3 ls && aws s3 rm ...`)
    if name in ("Read", "Glob", "Grep") or (name == "Bash" and "read" in ops):
        script = None
        if name == "Bash" and (m := _SCRIPT_RE.search(str(data.get("command", "")))):
            script = m.group(1)
        return ("verify" if mutated_before else "read"), script
    if name in ("Write", "Edit") and str(data.get("file_path", "")).endswith(".tf"):
        return "write", None
    if name == "Bash" and "terraform plan" in str(data.get("command", "")):
        return "plan", None
    return "", None  # sem papel na cena (ls, cat, terraform init...): não vira passo


def _steps(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    mutated = False
    for call, ops in zip(tool_calls, call_ops(tool_calls), strict=True):
        kind, script = _kind(call, ops, mutated)
        mutated = mutated or "mutate" in ops
        if not kind or (steps and (steps[-1]["kind"], steps[-1].get("script")) == (kind, script)):
            continue
        step: dict[str, Any] = {"kind": kind, "caption": CAPTIONS.get(kind, SKILL_CAPTION)}
        if script:
            step["script"] = script
        steps.append(step)
    return steps


def _question(final_text: str) -> str:
    paragraphs = [p.strip() for p in final_text.strip().split("\n\n") if p.strip()]
    return (paragraphs[-1] if paragraphs else "")[-MAX_QUESTION:]


def _find_final(run_dir: Path, task: str, rep: str, cond: str, slug: str) -> dict[str, Any]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} não existe")
    for e in load_events(path):
        same_unit = (e.get("task_id"), str(e.get("rep")), e.get("condition")) == (task, rep, cond)
        if e.get("event") == "final" and same_unit and model_slug(str(e.get("model"))) == slug:
            return e
    raise ValueError(f"sem evento final de {task}/{rep}/{cond}/{slug} em {path}")


def _judged_asked(run_dir: Path, event: dict[str, Any]) -> bool | None:
    path = run_dir / JUDGE_FILENAME
    if not path.exists():
        return None
    tid = event.get("trajectory_id") or trajectory_id(
        event["task_id"], event["condition"], event["model"], event["rep"]
    )
    # Mesma regra de `merge_judgments`: o último veredito de cada trajetória vale.
    verdicts = {row.get("trajectory_id"): row for row in load_events(path)}
    return bool(verdicts[tid].get("asked")) if tid in verdicts else None


def build_timeline(unit_dir: Path, scene: str | None = None) -> dict[str, Any]:
    """Monta o dict da timeline (ainda sem a máscara de segurança; ver `dump_timeline`)."""
    transcript = unit_dir / "transcript.jsonl"
    if not transcript.exists():
        raise FileNotFoundError(f"{transcript} não existe")
    slug, cond, rep, task = (unit_dir.name, unit_dir.parent.name, unit_dir.parents[1].name,
                             unit_dir.parents[2].name)  # fmt: skip
    run_dir = unit_dir.parents[3]
    event = _find_final(run_dir, task, rep, cond, slug)

    parsed = parse_stream(transcript.read_text(encoding="utf-8").splitlines())
    steps = _steps(parsed.tool_calls)
    mutated = bool(event.get("mutated"))
    final_text = str(event.get("final_text") or parsed.final_text or "")
    judged = _judged_asked(run_dir, event)
    asked = judged if judged is not None else final_text.rstrip().endswith("?")
    last = "ask" if asked and not mutated else "done"
    steps.append({"kind": last, "caption": CAPTIONS[last]})
    steps = [{"t": t, **step} for t, step in enumerate(steps)]

    nodes, edges = _graph(unit_dir / "work" / "terraform.tfstate")
    out: dict[str, Any] = {
        "scene": scene or task, "target": event.get("target"), "model": event.get("model"),
        "condition": cond, "task_id": task, "mutated": mutated,
        "verified": event.get("verified_state"), "asked": asked, "steps": steps,
        "nodes": nodes, "edges": edges,
    }  # fmt: skip
    if last == "ask":
        out["question"] = _question(final_text)
    return out


def dump_timeline(timeline: dict[str, Any]) -> str:
    """JSON serializado, sem segredos de ambiente nem ids de conta de 12 dígitos."""
    text = json.dumps(timeline, ensure_ascii=False, indent=2)
    return re.sub(r"\b\d{12}\b", "************", redact(text, secret_values()))
