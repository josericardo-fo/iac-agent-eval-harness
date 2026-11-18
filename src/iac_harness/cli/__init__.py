"""CLI: modality-* | ambig-prevalidate | agent-* | demo-timeline. Mapeamentos simples."""

from __future__ import annotations

import argparse
from pathlib import Path

from iac_harness.agentic.judge import JUDGE_FILENAME
from iac_harness.agentic.question_judge import QUESTIONS_FILENAME
from iac_harness.cli.agent import (
    cmd_agent_judge,
    cmd_agent_judge_questions,
    cmd_agent_metrics,
    cmd_agent_questions_report,
    cmd_agent_resume,
    cmd_agent_run,
)
from iac_harness.cli.demo import cmd_demo_timeline
from iac_harness.cli.modality import (
    REPLAY_CATEGORIES,
    cmd_ambig_prevalidate,
    cmd_modality_metrics,
    cmd_modality_prevalidate,
    cmd_modality_replay,
    cmd_modality_run,
)
from iac_harness.modalities.spec import MODALITIES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iac-harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mpre = sub.add_parser(
        "modality-prevalidate",
        help="checa referência e estado inicial de cada tarefa × modalidade (Terraform/CLI/boto3)",
    )
    p_mpre.add_argument("--config", type=Path, required=True)
    p_mpre.add_argument("--tasks", nargs="+")
    p_mpre.set_defaults(func=cmd_modality_prevalidate)

    p_apre = sub.add_parser(
        "ambig-prevalidate",
        help="E16: gate do Ambig-IaC (referência passa no oráculo, main.tf vazio reprova)",
    )
    p_apre.add_argument("--config", type=Path, required=True)
    p_apre.add_argument("--order", type=Path, help="selection.json com a ordem do sorteio")
    p_apre.add_argument("--tasks", nargs="+", help="tarefas avulsas (no lugar de --order)")
    p_apre.add_argument("--target-pass", type=int, help="para ao juntar N aprovadas")
    p_apre.set_defaults(func=cmd_ambig_prevalidate)

    p_mrun = sub.add_parser(
        "modality-run", help="roda o agente nas modalidades (Terraform/CLI/boto3)"
    )
    p_mrun.add_argument("--config", type=Path)
    p_mrun.add_argument("--tasks", nargs="+")
    p_mrun.add_argument("--modalities", nargs="+", choices=list(MODALITIES))
    p_mrun.add_argument("--reps", type=int)
    p_mrun.add_argument(
        "--resume",
        type=Path,
        metavar="RUN_DIR",
        help="retoma um run interrompido: pula (tarefa, modalidade, rep) com evento final",
    )
    p_mrun.set_defaults(func=cmd_modality_run)

    p_mrep = sub.add_parser(
        "modality-replay",
        help="reexecuta sem LLM o artefato final de cada trajetória e mede o efeito colateral",
    )
    p_mrep.add_argument("src_run_dir", type=Path, help="run dir de um modality-run")
    p_mrep.add_argument("--tasks", nargs="+")
    p_mrep.add_argument(
        "--categories",
        type=lambda v: tuple(v.split(",")),
        default=REPLAY_CATEGORIES,
        help="categorias separadas por vírgula (padrão: modify,query)",
    )
    p_mrep.set_defaults(func=cmd_modality_replay)

    p_mmet = sub.add_parser(
        "modality-metrics", help="métricas de um modality-run (ou resumo de um modality-replay)"
    )
    p_mmet.add_argument("run_dir", type=Path)
    p_mmet.add_argument("--format", choices=("json", "table"), default="json")
    p_mmet.add_argument(
        "--exclude-tasks",
        nargs="+",
        metavar="TASK",
        help="remove essas tarefas (task_id) antes de calcular as métricas",
    )
    p_mmet.add_argument(
        "--baseline",
        type=Path,
        metavar="RUN_DIR",
        help="run de forma fixa para comparar com um run da forma livre (E4)",
    )
    p_mmet.set_defaults(func=cmd_modality_metrics)

    p_arun = sub.add_parser(
        "agent-run", help="E3: roda o Claude Code de verdade (headless, em container) nas tarefas"
    )
    p_arun.add_argument("--config", type=Path, required=True)
    p_arun.add_argument("--tasks", nargs="+")
    p_arun.add_argument("--reps", type=int)
    p_arun.set_defaults(func=cmd_agent_run)

    p_ares = sub.add_parser(
        "agent-resume",
        help="retoma um agent-run: pula (tarefa, condição, modelo, rep) com evento final",
    )
    p_ares.add_argument("run_dir", type=Path)
    p_ares.set_defaults(func=cmd_agent_resume)

    p_ajud = sub.add_parser(
        "agent-judge", help="juiz LLM: a mensagem final do agente pergunta algo? (judge.jsonl)"
    )
    p_ajud.add_argument("run_dir", type=Path, nargs="?")
    p_ajud.add_argument("--model", help="modelo do juiz (padrão: [judge] model da config)")
    p_ajud.add_argument("--redo", action="store_true", help="reclassifica todas as trajetórias")
    p_ajud.add_argument(
        "--sample", type=int, help="grava judge_sample.csv com N trajetórias para leitura à mão"
    )
    p_ajud.add_argument("--seed", type=int, default=0, help="semente da amostra (--sample)")
    p_ajud.add_argument(
        "--agreement", type=Path, help="judge_sample.csv preenchido: concordância juiz × humano"
    )
    p_ajud.add_argument(
        "--provider", choices=("anthropic", "codex"), help="provedor do juiz (padrão: [judge])"
    )
    p_ajud.add_argument("--reasoning-effort", help="esforço de raciocínio do juiz (codex)")
    p_ajud.add_argument(
        "--out",
        default=JUDGE_FILENAME,
        help="arquivo de vereditos no run_dir (padrão: judge.jsonl)",
    )
    p_ajud.add_argument(
        "--compare-with",
        metavar="ARQUIVO",
        help="outro arquivo de vereditos no run_dir: concordância com judge.jsonl (sem LLM)",
    )
    p_ajud.set_defaults(func=cmd_agent_judge)

    p_ajq = sub.add_parser(
        "agent-judge-questions",
        help="E15: juiz LLM da qualidade das perguntas (asked=true) -> judge-questions.jsonl",
    )
    p_ajq.add_argument("run_dir", type=Path)
    p_ajq.add_argument(
        "--provider", choices=("anthropic", "codex"), help="provedor do juiz (padrão: [judge])"
    )
    p_ajq.add_argument("--model", help="modelo do juiz (padrão: o do provedor / [judge])")
    p_ajq.add_argument(
        "--out",
        default=QUESTIONS_FILENAME,
        help=f"arquivo no run_dir (padrão: {QUESTIONS_FILENAME})",
    )
    p_ajq.add_argument("--limit", type=int, help="nota no máximo N trajetórias (smoke)")
    p_ajq.set_defaults(func=cmd_agent_judge_questions)

    p_aqr = sub.add_parser(
        "agent-questions-report",
        help="E15: notas das perguntas por condição, toolkit × none, kappa entre juízes",
    )
    p_aqr.add_argument("run_dirs", type=Path, nargs="+")
    p_aqr.add_argument(
        "--judge-file", default=QUESTIONS_FILENAME, help=f"notas (padrão: {QUESTIONS_FILENAME})"
    )
    p_aqr.add_argument(
        "--compare", nargs=2, metavar=("A", "B"),
        help="dois arquivos de notas no run_dir: kappa ponderado e desacordos de 2 pontos",
    )  # fmt: skip
    p_aqr.set_defaults(func=cmd_agent_questions_report)

    p_amet = sub.add_parser(
        "agent-metrics", help="métricas de um agent-run (condição × modelo, estratos, alvo)"
    )
    p_amet.add_argument("run_dir", type=Path)
    p_amet.add_argument("--format", choices=("json", "table"), default="json")
    p_amet.add_argument(
        "--judge-file", default=JUDGE_FILENAME, help="arquivo de vereditos (padrão: judge.jsonl)"
    )
    p_amet.add_argument(
        "--compare", type=Path, help="outro agent-run (ex.: AWS real): concordância do sucesso"
    )
    p_amet.set_defaults(func=cmd_agent_metrics)

    p_demo = sub.add_parser(
        "demo-timeline", help="trajetória gravada -> timeline.json para o vídeo da demo"
    )
    p_demo.add_argument("unit_dir", type=Path, help="runs/<run>/<task>/<rep>/<cond>/<modelo>")
    p_demo.add_argument("-o", "--output", type=Path, help="arquivo de saída (padrão: stdout)")
    p_demo.add_argument("--scene", help="nome da cena (padrão: task_id)")
    p_demo.set_defaults(func=cmd_demo_timeline)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
