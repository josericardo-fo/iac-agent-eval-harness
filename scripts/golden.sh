#!/usr/bin/env bash
# Teste de ouro (docs/plano-framework.md §7): roda os leitores (agent-metrics,
# agent-questions-report, modality-metrics, demo-timeline) sobre runs gravados em runs/
# e compara a saída com um snapshot. A refatoração não pode mudar nenhum byte.
#
# Uso:
#   scripts/golden.sh capture   # grava o snapshot em runs/golden/ (faça na tag v1.0.0)
#   scripts/golden.sh check     # roda de novo e compara com o snapshot (diff; sai 1 se mudou)
#
# Local: depende dos runs em runs/ (fora do git). Run ausente é erro, não pulo silencioso.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO" || exit 1
SNAPSHOT="$REPO/runs/golden"
R=runs

# nome do caso | comando (argumentos do iac-harness)
CASES=(
  "agent-metrics-e13-json|agent-metrics $R/20261114T194733-e13-real-aws --format json"
  "agent-metrics-e13-table|agent-metrics $R/20261114T194733-e13-real-aws --format table"
  "agent-metrics-e13-luna|agent-metrics $R/20261114T194733-e13-real-aws --judge-file judge-luna.jsonl --format json"
  "agent-metrics-e10-table|agent-metrics $R/20261112T172414-e10-lay-haiku --format table"
  "agent-metrics-e16-json|agent-metrics $R/20261116T125901-e16-ambig-iac --format json"
  "agent-metrics-e13floci-json|agent-metrics $R/20261112T181628-e13-usersim-floci --format json"
  "agent-metrics-compare|agent-metrics $R/20261106T170449-e3-agent-sonnet --compare $R/20261109T163420-e3-agent-aws --format json"
  "questions-report|agent-questions-report $R/20261106T143311-e3-agent-haiku $R/20261106T170449-e3-agent-sonnet $R/20261112T172414-e10-lay-haiku"
  "questions-report-kappa|agent-questions-report $R/20261114T194733-e13-real-aws --compare judge-questions.jsonl judge-questions-luna.jsonl"
  "modality-metrics-json|modality-metrics $R/20261027T150905-modality-haiku --format json"
  "modality-metrics-table|modality-metrics $R/20261027T150905-modality-haiku --format table"
  "modality-metrics-replay|modality-metrics $R/20261029T191956-replay-20261027T150905-modality-haiku --format json"
  "modality-metrics-ask|modality-metrics $R/20261030T131533-e2-ask-haiku --format json"
  "modality-metrics-free|modality-metrics $R/20261030T153918-e4-free-haiku --baseline $R/20261027T150905-modality-haiku --format table"
  "demo-timeline-e13|demo-timeline $R/20261114T194733-e13-real-aws/real-ssh-vpn-ip/2/own-rules-v2/Sonnet-5.5"
)

run_cases() {
  local out=$1 failed=0
  mkdir -p "$out"
  for c in "${CASES[@]}"; do
    local name=${c%%|*} cmd=${c#*|}
    # shellcheck disable=SC2086  # o comando é dividido em argumentos de propósito
    if ! uv run --quiet iac-harness $cmd >"$out/$name.out" 2>"$out/$name.err"; then
      echo "ERRO: $name (veja $out/$name.err)" >&2
      failed=1
    fi
  done
  return $failed
}

case "${1:-}" in
  capture)
    rm -rf "$SNAPSHOT"
    run_cases "$SNAPSHOT" || exit 1
    git rev-parse HEAD >"$SNAPSHOT/COMMIT"
    echo "snapshot gravado em $SNAPSHOT ($(git rev-parse --short HEAD), ${#CASES[@]} casos)"
    ;;
  check)
    [ -d "$SNAPSHOT" ] || { echo "sem snapshot; rode: $0 capture" >&2; exit 2; }
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    run_cases "$TMP" || exit 1
    # Compara só os casos de CASES: o snapshot pode ter casos que já saíram (ex.: metrics-main-n3).
    mismatch=0
    for c in "${CASES[@]}"; do
      name=${c%%|*}
      diff "$SNAPSHOT/$name.out" "$TMP/$name.out" || mismatch=1
    done
    if [ "$mismatch" -eq 0 ]; then
      echo "ouro OK: ${#CASES[@]} casos idênticos ao snapshot de $(cut -c1-7 "$SNAPSHOT/COMMIT")"
    else
      echo "ouro FALHOU: a saída mudou em relação ao snapshot" >&2
      exit 1
    fi
    ;;
  *)
    echo "uso: $0 capture|check" >&2
    exit 2
    ;;
esac
