#!/usr/bin/env bash
# E15 (registro-metodologico.md §1): juiz da qualidade das perguntas nos runs do E14.
# 1) Sonnet em todos os runs (judge-questions.jsonl); 2) gpt-6-luna pelo Codex, um run por vez
# (judge-questions-luna.jsonl); 3) relatório com os dois juízes em runs/e15-report.md.
# Retomável: rodar de novo pula as trajetórias que já têm nota. Falha num run não para os outros.
#
# Uso: scripts/e15_overnight.sh   (não rode junto com outro job do Codex)
set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO" || exit 1
LOG="$REPO/runs/e15-overnight.log"
REPORT="$REPO/runs/e15-report.md"
RUNS=(
  runs/20261106T143311-e3-agent-haiku
  runs/20261106T170449-e3-agent-sonnet
  runs/20261106T192631-e3-fix118-haiku
  runs/20261106T192914-e3-fix118-sonnet
  runs/20261110T144440-e3-skillfix
  runs/20261109T163420-e3-agent-aws
  runs/20261111T132723-e11-amb-aws
  runs/20261111T162415-e12-v2-aws
  runs/20261112T172414-e10-lay-haiku
  runs/20261114T194733-e13-real-aws
)

# Credenciais do gateway Anthropic (o conteúdo nunca vai para o log).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >>"$LOG"; }

judge_all() {  # $@: opções extras do agent-judge-questions
  for run in "${RUNS[@]}"; do
    log "início $run $*"
    if uv run iac-harness agent-judge-questions "$run" "$@" >>"$LOG" 2>&1; then
      log "fim $run $*"
    else
      log "FALHA $run $* (código $?); segue para o próximo run"
    fi
  done
}

log "=== E15 overnight: Sonnet ==="
judge_all
log "=== E15 overnight: gpt-6-luna (Codex) ==="
judge_all --provider codex --out judge-questions-luna.jsonl
log "=== relatório -> $REPORT ==="
if uv run iac-harness agent-questions-report "${RUNS[@]}" \
  --compare judge-questions.jsonl judge-questions-luna.jsonl >"$REPORT" 2>>"$LOG"; then
  log "relatório ok"
else
  log "FALHA no relatório (código $?)"
fi
log "=== E15 overnight: fim ==="
