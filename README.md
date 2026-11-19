# iac-agent-eval-harness

A framework for evaluating coding agents that operate a cloud account. A real production agent
(Claude Code, headless, in a container) gets a task on an AWS emulator or a real AWS account; the
harness resets the backend, plants the initial state, runs the agent with or without a toolkit,
and grades the final cloud state with a deterministic verifier, plus LLM judges and an optional
simulated user for ambiguous requests.

It came out of a research residency on agentic systems for cloud infrastructure engineering. The
results, the toolkit and the method are documented in Portuguese under [`docs/`](docs/)
(summary in Portuguese: [docs/framework.md](docs/framework.md)).

**Who it is for:** researchers and engineers who want to measure whether an agent (or a skill,
rule set, plugin or MCP server given to it) actually leaves the cloud in the right state, asks
when the request is ambiguous, and does not touch what it should not.

## What it combines

| Feature | Where in the code |
|---|---|
| Real production agent (Claude Code headless, in a container, egress only through a proxy) | `agentic/agents.py`, `docker/agent.Dockerfile`, `docker/egress/` |
| The same task in Terraform, AWS CLI and boto3 | `modalities/` |
| Toolkit (skill, rules, plugin, MCP) as a controlled variable; a test guarantees the distributed plugin carries the same text as the measured condition | `agentic/conditions.py`, `toolkit/`, `tests/test_toolkit.py` |
| Emulator × real AWS agreement on the same tasks and verifiers | `agent-metrics --compare`, `executors/floci.py`, `executors/aws.py` |
| Simulated user for ambiguous cloud requests (hidden `user.md`) | `agentic/user_sim.py` |
| Validated LLM judges (human sample, second judge, kappa) | `agentic/judge.py`, `agentic/question_judge.py`, `agent-judge --agreement/--compare-with` |
| Real AWS reset by inventory (baseline + sweep) | `executors/aws_sweep.py` |
| Provenance in every run (git commit, image ids and digests, tool versions, prompt versions) | `cli/provenance.py` (`_git_provenance`, `_sandbox_provenance`), `cli/agent.py` (`_agent_run_config`) |

Paths are relative to `src/iac_harness/`.

## Components

| Term | Meaning | Current name in the code |
|---|---|---|
| Task | directory with request, initial state and verifier | `modalities/tasks/<id>/` ([format](docs/tarefas.md)) |
| Dataset | set of tasks (IaC-Eval v2, aws-bench, authored, real-world cases, Ambig-IaC) | task id prefixes `task-`, `awsb-`, `mod-`, `amb-`, `real-`, `lay-`… |
| Backend | where the cloud runs: Floci emulator or a real AWS account | `executors/`, `agentic/targets.py` |
| Agent | who solves the task | `agentic/agents.py` (`ClaudeCodeAgent`) |
| Toolkit | what the agent is given: nothing, skill, rules, plugin | `agentic/conditions.py` (`REGISTRY`) |
| Verifier | checks the final state, no LLM | task `check.py` / `check_final.py` |
| Judge | LLM classification (did it ask? how good was the question?) | `agentic/judge.py`, `agentic/question_judge.py` |
| User Simulator | LLM that answers from the hidden `user.md` | `agentic/user_sim.py` |
| Trial | one execution (task × rep × toolkit × model) | `runs/<id>/<task>/<rep>/<cond>/<model>/` |

Architecture diagram, CLI map and what is recorded per trial: [docs/framework.md](docs/framework.md).

## Quickstart: one trial on Floci

Requirements: Docker, [uv](https://docs.astral.sh/uv/), and an Anthropic-compatible gateway
(`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`). The model IDs in the configs (`Haiku 5.5`,
`Sonnet 5.5`) are the IDs that gateway accepts; adjust them to yours. The CLI does not read
`.env`: export the variables.

```bash
uv sync
uv run pytest                                   # unit tests, no Docker

docker build -f docker/toolchain.Dockerfile -t iac-harness-toolchain:latest .
docker build -f docker/agent.Dockerfile -t iac-harness-agent:latest .

export ANTHROPIC_BASE_URL=... ANTHROPIC_AUTH_TOKEN=...
docker compose -f docker/compose.yaml --profile agent up -d --wait   # Floci + egress proxy

uv run iac-harness agent-run --config configs/quickstart-floci.toml # prints runs/<id>
uv run iac-harness agent-judge runs/<id>                            # LLM judge: did it ask?
uv run iac-harness agent-metrics runs/<id> --format table

docker compose -f docker/compose.yaml --profile agent down
```

[`configs/quickstart-floci.toml`](configs/quickstart-floci.toml) runs one task
(`mod-sqs-retention`) under two conditions, `none` and `own-rules-v2` (the distributed toolkit),
with one model and one repetition: two trials. The real cost per trial is recorded in the
event (`cost_usd`) and shown by `agent-metrics`.

## Install the toolkit in Claude Code

The distributed toolkit is the `iac-cloud` plugin (skill + rules: inventory before acting, ask
when the request is ambiguous, verify the final state through the API). It carries the same text
as the measured condition `own-rules-v2`. The other toolkits in `toolkit/` (v1 in `toolkit/src/`, v3 in
`toolkit/src-v3/`, and the `own-rules`, `own-rules-v3` and `own-tools` plugins) stay in the repo
only because the registered conditions build them; they are not distributed.

```bash
claude plugin marketplace add josericardo-fo/iac-agent-eval-harness
claude plugin install iac-cloud@iac-cloud-toolkit
```

Or, inside a session: `/plugin install iac-cloud --marketplace josericardo-fo/iac-agent-eval-harness`.

In the harness the rules enter through `--append-system-prompt-file`; in the plugin a
`SessionStart` hook adds them to the session context. That delivery path was not measured
(details in [docs/pesquisa.md](docs/pesquisa.md)). Each rule is traced to a finding in
[docs/regras-achados.md](docs/regras-achados.md).

## Documentation (Portuguese)

- [docs/framework.md](docs/framework.md): summary, architecture, finding → rule → measure cycle,
  what is recorded, CLI map, positioning, limitations.
- [docs/tarefas.md](docs/tarefas.md): task format, datasets, how to create and pre-validate a task.
- [docs/componentes.md](docs/componentes.md): simulated user, judges, real AWS sweep, backends.
- [docs/registro-metodologico.md](docs/registro-metodologico.md): pre-registered decisions and
  results (the source of truth); [docs/guia-decisao.md](docs/guia-decisao.md): decision guide.
- [docs/pesquisa.md](docs/pesquisa.md): research history and per-experiment usage;
  [configs/README.md](configs/README.md): which config produced which run.

## Limitations

- AWS only. One agent (Claude Code); the LLM layer talks to an Anthropic gateway or the Codex CLI.
- The default judge is Anthropic (`Sonnet 5.5`); the simulated user defaults to the Codex CLI
  (`gpt-6-luna`), which needs a logged-in `codex`.
- The real AWS backend deletes everything outside its baseline in the scoped regions and costs
  money; read [docs/componentes.md](docs/componentes.md) before using it.
- The official AWS Emulator was not evaluated. LocalStack (now token-only) was removed; the spike
  that used it is in tag `v1.0.0`.
- The modality prompts pin Terraform ≥ 1.15 and `hashicorp/aws ~> 6.0`.
- The feedback-in-the-loop pipeline of the early research (conditions A/B/C) was removed; it is in
  tag `v1.0.0`.
- Names still carry research history (experiment ids in configs, `agentic/`, `modalities/`); the
  renaming plan is in [docs/plano-framework.md](docs/plano-framework.md).

## Reproducibility

The commands, configs and run ids cited in
[docs/registro-metodologico.md](docs/registro-metodologico.md) are valid for tag **`v1.0.0`**,
not for `HEAD`: after the tag the code may be refactored (without changing recorded values). To
reproduce a registered run, `git checkout v1.0.0`. Runs live in `runs/` (outside git); each one
records its config, git commit, image digests and prompt versions in `run_config.json`.

Third-party code under `third_party/` keeps its own LICENSE and NOTICE.
