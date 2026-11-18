# História da pesquisa e uso por experimento

Este documento guarda o que era o README até 18/11/2026: o estado da pesquisa da Residência, as
fases, os experimentos e o uso dos comandos por experimento. O README passou a apresentar o
harness como framework ([../README.md](../README.md)); a arquitetura e o mapa da CLI estão em
[framework.md](framework.md).

**Reprodutibilidade.** Os comandos e configs citados aqui e no
[registro-metodologico.md](registro-metodologico.md) valem para a tag `v1.0.0`, não para o `HEAD`
(depois da tag, o código pode ser refatorado; ver [plano-framework.md](plano-framework.md)).
Para reproduzir um run registrado: `git checkout v1.0.0`.

## Pergunta

Harness para avaliar agentes que mantêm infraestrutura AWS. Pergunta: em que casos usar cada forma
(Terraform, AWS CLI, boto3), por tipo de caso e dificuldade. O projeto fecha com um toolkit
próprio (skill + regras) para ajudar agentes a manter a nuvem de pessoas leigas, com cada regra
ligada a um achado ([regras-achados.md](regras-achados.md)).

## Estado

O projeto teve três fases. Todas usam o mesmo harness (emulador Floci ou AWS real, toolchain em
Docker, procedência registrada em cada run):

1. **Feedback no loop (Semanas 4–6):** quanto validate, plan, OPA e sandbox ajudam um agente que
   gera Terraform (IaC-Eval v2, condições A/B/C). Protocolo em [protocolo.md](protocolo.md).
2. **Modalidades (Semanas 6–8):** a mesma tarefa em Terraform, CLI e boto3, no Floci e na AWS
   real. Nenhuma forma domina; a forma muda o custo em tokens, não o acerto.
3. **Toolkit (Semanas 8–10):** experimentos E1–E4 e E10–E18, incluindo o Claude Code de verdade
   com 5 condições de toolkit. O toolkit troca chute por pergunta nos pedidos ambíguos e não perde
   nas claras; a v2 (`toolkit/src-v2/`) substitui a v1 e é a distribuída. Nos casos reais com
   usuário simulado (E13), `none` acerta 2/27 e a v2 11/27 no 1º turno (12/27 depois da
   resposta); com o toolkit, as perguntas saem um pouco melhores (E15). No Ambig-IaC (E16–E18),
   nenhum toolkit recupera o que a ambiguidade custa: o toolkit ajuda quando falta o tipo de
   recurso ou o alvo, não quando faltam atributos. A v3 (`toolkit/src-v3/`) fica como achado. O
   E19 não foi rodado.

O desenvolvimento congela no gate da Semana 10 (19/11/2026).

Onde ler:

- Decisões de método e resultados: [registro-metodologico.md](registro-metodologico.md).
  A seção "Experimentos" mapeia experimento → config → run.
- Guia de decisão (que forma usar e quando usar o toolkit, só com o que tem medida):
  [guia-decisao.md](guia-decisao.md).
- Configs por fase: [../configs/README.md](../configs/README.md).
- Os runs ficam em `runs/` (fora do git).

## Toolkit no Claude Code: diferença em relação ao experimento

O toolkit distribuído é o plugin `iac-cloud`, com o mesmo texto da condição `own-rules-v2`
medida no E11/E12 (um teste garante isso). A instalação está no README.

**Diferença em relação ao experimento:** no harness, o `RULES.md` entra pelo
`--append-system-prompt-file`. Num plugin isso não é possível, então um hook `SessionStart` põe
o mesmo texto no contexto da sessão. A regra chega ao agente, mas por outro caminho, e esse
caminho não foi medido. O efeito esperado vem do E3 e do E11/E12: com a regra, o Haiku aciona a
skill sempre; sem ela, só em parte dos pedidos.

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

Existe também o [AWS Emulator](https://github.com/aws/aws-emulator), emulador oficial da AWS lançado
em 11/11/2026. Ele não foi avaliado neste estudo e fica como alternativa ao Floci em trabalho futuro.

Chaves de `[sandbox]`: `endpoint` (visão da toolchain), `host_endpoint` (visão do host, padrão
`http://127.0.0.1:4566`), `container` (padrão `iac-harness-floci-1`) e `region`.

### 3. Pré-validar as referências (pipeline de feedback no loop)

> Os comandos `prevalidate`, `run` e `metrics` dos passos 3–5 e as configs citadas
> (`smoke.toml`, `mvp.toml`) existem só na tag `v1.0.0` (`git checkout v1.0.0`); o pipeline de
> feedback no loop saiu do código depois dela.

```bash
uv run iac-harness prevalidate --config configs/smoke.toml --limit 3
uv run iac-harness prevalidate --config configs/mvp.toml --tasks aws/task-269
```

Grava `runs/<id>/prevalidation.jsonl` (status por estágio, `harness_valid`, `sandbox_supported`) e
`prevalidation_summary.json` (Floci coverage), mais `prevalidation_config.json` com a procedência.
O dataset é baixado para `data/` na primeira vez.

### 4. Rodar o agente (pipeline de feedback no loop)

```bash
export ANTHROPIC_API_KEY=...   # a CLI não lê .env: exporte (ex.: set -a; source .env)
uv run iac-harness run --config configs/smoke.toml --tasks aws/task-269 --reps 1 \
    --prevalidation runs/<id>/prevalidation.jsonl   # ou --no-prevalidation (smoke)
```

Grava `runs/<id>/events.jsonl` e `run_config.json` (config, procedência, `prompt_version` e o sha256
da pré-validação). `--prevalidation` é obrigatório; só `--no-prevalidation` (smoke) permite rodar
sem ela, assumindo `harness_valid` e `sandbox_supported` verdadeiros.

### 5. Métricas (pipeline de feedback no loop)

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
não está nele (`executors/aws_sweep.py`, rodado na toolchain): EC2 (com volumes e snapshots),
RDS sem snapshot final, Lightsail, MSK, S3 (com versões), DynamoDB, SQS, SNS, parâmetros SSM,
Secrets Manager sem recuperação, chaves KMS do cliente (exclusão agendada em 7 dias), usuários/grupos
IAM `del-*`/`mod-*` e, em `us-west-2`, EIPs, volumes e snapshots (E13). Recursos da VPC
default ficam intactos. Se sobrar algo fora do baseline, o comando aborta.
Ao fim de `modality-prevalidate`/`modality-run` (inclusive com erro), uma limpeza final apaga o
que o último par criou. A lista completa de categorias e os riscos estão em
[componentes.md](componentes.md#sweep-da-aws-real).

O baseline fica em `[sandbox] baseline_file` (padrão `runs/aws-baseline.json`) e representa a
conta limpa: é reaproveitado entre runs e no `--resume`, para que as sobras de um run que caiu
sejam apagadas. Apagar o arquivo faz o próximo run tirar um inventário novo da conta; só faça isso
com a conta limpa, ou as sobras viram baseline e nunca são apagadas.

Premissas:
- não crie nada em `us-east-1` nem EC2 em `us-west-2` (nem usuários/grupos IAM `del-*`/`mod-*`)
  durante um run real: o que aparecer fora do baseline é apagado;
- custa dinheiro: RDS, EC2 e sobretudo MSK cobram por hora; confira o Cost Explorer depois de
  cada run (orçamento da conta: US$ 20 com alertas);
- a task-201 (MSK) roda à parte, e as tarefas 108, 110, 115 e 118 falham por desenho
  ([datasets.md](datasets.md)).

```bash
set -a; source .env; set +a   # IAC_AWS_* (a CLI não lê .env)
uv run iac-harness modality-prevalidate --config configs/modality-real-aws.toml \
    --tasks mod-sqs-retention del-s3-bucket
```

O resultado de cada limpeza (apagados, falhas, restantes) sai no stderr como `aws sweep: {...}`.

## Estudo de modalidades (Terraform, AWS CLI, boto3)

O agente resolve a mesma tarefa em cada forma; a nota vem do estado final dos recursos
(`check.py` da tarefa). Desenho e decisões: [registro-metodologico.md](registro-metodologico.md).

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
  agente perguntou com um bloco ` ```question.md ` que o parser antigo não lia. Comando de uso
  único: existe só na tag `v1.0.0` (`git checkout v1.0.0`).

## E3: agente real (Claude Code headless em container)

O Claude Code (`claude -p`) age sozinho no Floci ou na AWS real, sob 5 condições de toolkit
(`none`, `aws-core`, `own-skill-only`, `own-rules`, `own-tools`), mais `own-rules-v2` desde o
E11/E12 (texto em `toolkit/src-v2/`; pela regra de decisão do registro, a v2 substitui a v1). Código
em `src/iac_harness/agentic/`; o toolkit próprio sai de `toolkit/src/` e `toolkit/src-v2/` com
`uv run python toolkit/build.py`.

```bash
docker build -f docker/agent.Dockerfile -t iac-harness-agent:latest .
set -a; source .env; set +a
docker compose -f docker/compose.yaml --profile agent --profile agent-workers up -d --wait

uv run iac-harness agent-run --config configs/e3-agent-haiku.toml   # --tasks/--reps para recortar
uv run iac-harness agent-resume runs/<id>                           # pula o que já tem evento final
uv run iac-harness agent-judge runs/<id>                            # juiz: a mensagem final pergunta algo?
uv run iac-harness agent-judge runs/<id> --sample 30 --seed 20261106   # judge_sample.csv para leitura à mão
uv run iac-harness agent-judge runs/<id> --agreement runs/<id>/judge_sample.csv   # juiz × humano
uv run iac-harness agent-judge runs/<id> --provider codex --out judge-luna.jsonl   # 2º juiz (gpt-6-luna, E14)
uv run iac-harness agent-judge runs/<id> --compare-with judge-luna.jsonl   # juiz × juiz (kappa)
uv run iac-harness agent-metrics runs/<id> --format table           # condição × modelo, estratos, alvo
uv run iac-harness agent-metrics runs/<floci> --compare runs/<aws>  # concordância Floci × AWS real
uv run iac-harness demo-timeline runs/<id>/<task>/<rep>/<cond>/<modelo> -o cena.json   # timeline da demo animada
```

- O agente roda como usuário não-root, com egresso só pelo proxy squid (perfil `agent`).
- Os estratos (intenção, categoria, origem, dificuldade, alvo) ficam em `configs/strata.toml`.
- Usuário simulado (E13, opcional): com `[user_sim]` na config, quando o juiz classifica o 1º turno
  como `ask`, um modelo (`provider = "codex"`, `model = "gpt-6-luna"`, Codex CLI logado) responde a
  partir do `user.md` oculto da tarefa e a sessão é retomada (`max_replies`, padrão 1). O
  `check_final.py` opcional confere o estado depois da resposta (`success_final`). `[judge] provider`
  escolhe o fornecedor do juiz (padrão `anthropic`); `[modality] tasks_dir` troca o catálogo de
  tarefas. Smoke: `configs/e13-usersim-floci.toml`.
- Os testes de integração fazem `docker compose down`: não rode com um run em andamento.
