# iac-agent-eval-harness

Harness para medir o ganho incremental de feedback (validate, plan, OPA e sandbox Floci) no loop de
um agente que gera Terraform, usando o IaC-Eval v2. O protocolo completo está em
[docs/protocolo.md](docs/protocolo.md).

## Estado

Harness v1: dataset IaC-Eval v2, backend Anthropic, toolchain Docker, verificadores (validate, plan,
OPA), executor Floci com reset por restart, procedência registrada (git, imagens, providers) e CLI
(`prevalidate`, `run`, `metrics`, com controle pass@k).

## Uso

```bash
uv sync
uv run ruff check && uv run ruff format --check
uv run pytest                  # exclui testes de integração por padrão
uv run pytest -m integration   # sobe o compose; exige Docker e a imagem da toolchain
```

### 1. Construir a imagem da toolchain

```bash
docker build -f docker/toolchain.Dockerfile -t iac-harness-toolchain:latest .
```

A imagem traz Terraform (1.15.x exato, `ARG TERRAFORM_VERSION`), OPA 1.16.1 e um filesystem mirror
com os providers fixados (`aws` 6.66.0, `archive` 2.8.1 e `random` 3.9.1, via `ARG`), então
`terraform init` funciona sem internet.

### 2. Subir o Floci

```bash
docker compose -f docker/compose.yaml up -d --wait
docker compose -f docker/compose.yaml down   # ao terminar
```

O Floci fica na rede `iac-sandbox` (`internal: true`); a porta 4566 é exposta só em 127.0.0.1.
A imagem do Floci está fixada por digest. A toolchain acessa `http://floci:4566`; o harness, no host,
usa `sandbox.host_endpoint` (127.0.0.1).

O reset é um `docker restart` do container `sandbox.container`, seguido de espera pelo health e da
criação da VPC default em todas as regiões. O `POST /_floci/state/reset` do Floci 2.1.0 não serve:
apaga a VPC default e não a recria (protocolo §9.2). Se o reset falhar, o comando aborta.

Chaves de `[sandbox]`: `endpoint` (visão da toolchain), `host_endpoint` (visão do host, padrão
`http://127.0.0.1:4566`), `container` (padrão `iac-harness-floci-1`) e `region`.

### 3. Pré-validar as referências

```bash
uv run iac-harness prevalidate --config configs/smoke.toml --limit 3
uv run iac-harness prevalidate --config configs/mvp.toml --tasks aws/task-269
```

Grava `runs/<id>/prevalidation.jsonl` (status por estágio, `harness_valid`, `sandbox_supported`) e
`prevalidation_summary.json` (Floci coverage), mais `prevalidation_config.json` com a procedência. O dataset é baixado para `data/` na primeira vez.

### 4. Rodar o agente

```bash
export ANTHROPIC_API_KEY=...   # a CLI não lê .env: exporte (ex.: set -a; source .env)
uv run iac-harness run --config configs/smoke.toml --tasks aws/task-269 --reps 1 \
    --prevalidation runs/<id>/prevalidation.jsonl   # ou --no-prevalidation (smoke)
```

Grava `runs/<id>/events.jsonl` e `run_config.json` (config, procedência, `prompt_version` e o sha256
da pré-validação). `--prevalidation`
é obrigatório; só `--no-prevalidation` (smoke) permite rodar sem ela, assumindo `harness_valid` e
`sandbox_supported` verdadeiros.

### 5. Métricas

```bash
uv run iac-harness metrics runs/<id>
```

Para rodar sem Docker (dev), use `toolchain.mode = "local"` com `terraform` e `opa` no PATH; o
endpoint do Floci deve então ser alcançável do host (por exemplo `http://127.0.0.1:4566`).

A chave da Anthropic vem da variável de ambiente (modelo em `.env.example`).

### AWS real (modalidades)

`[sandbox] kind = "aws"` troca o emulador pela conta AWS real (`configs/modality-real-aws.toml`, e
`configs/modality-real-aws-msk.toml` só para a task-201). A toolchain usa as credenciais
`IAC_AWS_ACCESS_KEY_ID`/`IAC_AWS_SECRET_ACCESS_KEY` do ambiente (passadas ao container só pelo
nome, nunca na linha de comando), sem `AWS_ENDPOINT_URL` e na rede `bridge`. Antes de tudo,
`aws sts get-caller-identity` precisa devolver `[sandbox] expected_arn`; senão o comando aborta.

O reset tira um inventário (baseline) na primeira chamada e, a cada tentativa, apaga tudo o que
não está nele (`executors/aws_sweep.py`, rodado na toolchain): EC2, RDS sem snapshot final,
Lightsail, MSK, S3 (com versões), DynamoDB, SQS, SNS, Secrets Manager sem recuperação, chaves KMS
do cliente (exclusão agendada em 7 dias) e usuários/grupos IAM `del-*`/`mod-*`. Recursos da VPC
default ficam intactos. Se sobrar algo fora do baseline, o comando aborta.
Ao fim de `modality-prevalidate`/`modality-run` (inclusive com erro), uma limpeza final apaga o
que o último par criou.

O baseline fica em `[sandbox] baseline_file` (padrão `runs/aws-baseline.json`) e representa a
conta limpa: é reaproveitado entre runs e no `--resume`, para que as sobras de um run que caiu
sejam apagadas. Apagar o arquivo faz o próximo run tirar um inventário novo da conta; só faça isso
com a conta limpa, ou as sobras viram baseline e nunca são apagadas.

Premissas:
- não crie nada em `us-east-1` (nem usuários/grupos IAM `del-*`/`mod-*`) durante um run real: o
  que aparecer fora do baseline é apagado;
- custa dinheiro: RDS, EC2 e sobretudo MSK cobram por hora; confira o Cost Explorer depois de
  cada run (orçamento da conta: US$ 20 com alertas);
- a task-201 (MSK) roda à parte, e as tarefas 108, 110, 115 e 118 falham por desenho
  (`docs/datasets.md`).

```bash
set -a; source .env; set +a   # IAC_AWS_* (a CLI não lê .env)
uv run iac-harness modality-prevalidate --config configs/modality-real-aws.toml \
    --tasks mod-sqs-retention del-s3-bucket
```

O resultado de cada limpeza (apagados, falhas, restantes) sai no stderr como `aws sweep: {...}`.

## Estudo de modalidades (Terraform, AWS CLI, boto3)

O agente resolve a mesma tarefa em cada forma; a nota vem do estado final dos recursos
(`check.py` da tarefa). Desenho e decisões: [docs/registro-metodologico.md](docs/registro-metodologico.md).

```bash
uv run iac-harness modality-prevalidate --config configs/modality-haiku.toml   # referência e estado inicial
uv run iac-harness modality-run --config configs/modality-haiku.toml           # --resume <run> retoma
uv run iac-harness modality-metrics runs/<id>
```

- **Forma livre (E4):** `modalities = ["free"]` (`configs/e4-free-haiku.toml`); o agente escolhe a
  forma a cada tentativa. Roda sozinha e sem `allow_question`. Compare com uma forma fixa:
  `modality-metrics <run> --baseline <run de forma fixa>`.
- **Efeito colateral por replay (E1):** `modality-replay <run>` reexecuta sem LLM o artefato final de
  cada trajetória (padrão: `--categories modify,query`) e mede `collateral_ok` à parte da nota.
  Resumo: `modality-metrics <run do replay>`.
- **Reclassificar perguntas (E2):** `modality-reask <run>` corta, sem LLM, as trajetórias em que o
  agente perguntou com um bloco ` ```question.md ` que o parser antigo não lia.

## E3: agente real (Claude Code headless em container)

O Claude Code (`claude -p`) age sozinho no Floci ou na AWS real, sob 5 condições de toolkit
(`none`, `aws-core`, `own-skill-only`, `own-rules`, `own-tools`). Código em
`src/iac_harness/agentic/`; o toolkit próprio sai de `toolkit/src/` com
`uv run python toolkit/build.py`.

```bash
docker build -f docker/agent.Dockerfile -t iac-harness-agent:latest .
set -a; source .env; set +a
export LOCALSTACK_AUTH_TOKEN=unused   # o compose exige a variável
docker compose -f docker/compose.yaml --profile agent --profile agent-workers up -d --wait

uv run iac-harness agent-run --config configs/e3-agent-haiku.toml   # --tasks/--reps para recortar
uv run iac-harness agent-resume runs/<id>                           # pula o que já tem evento final
uv run iac-harness agent-judge runs/<id>                            # juiz: a mensagem final pergunta algo?
uv run iac-harness agent-judge runs/<id> --sample 30 --seed 20261106   # judge_sample.csv para leitura à mão
uv run iac-harness agent-judge runs/<id> --agreement runs/<id>/judge_sample.csv   # juiz × humano
uv run iac-harness agent-metrics runs/<id> --format table           # condição × modelo, estratos, alvo
uv run iac-harness agent-metrics runs/<floci> --compare runs/<aws>  # concordância Floci × AWS real
uv run iac-harness demo-timeline runs/<id>/<task>/<rep>/<cond>/<modelo> -o cena.json   # timeline da demo animada
```

- O agente roda como usuário não-root, com egresso só pelo proxy squid (perfil `agent`).
- Os estratos (intenção, categoria, origem, dificuldade, alvo) ficam em `configs/strata.toml`.
- Os testes de integração fazem `docker compose down`: não rode com um run em andamento.
