# Componentes: usuário simulado, juízes, sweep da AWS real e backends

Os componentes que entram em volta do agente e do verificador. Os caminhos são relativos a
`src/iac_harness/`. A camada de LLM (`llm/`) tem dois provedores: `anthropic` (API compatível,
via `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`) e `codex` (Codex CLI logado no host). Cada
componente escolhe o seu na config.

## Usuário simulado

`agentic/user_sim.py`. Liga com uma seção `[user_sim]` na config do `agent-run`; sem ela, o
trial termina na primeira mensagem final do agente.

Com `[user_sim]`, depois do `check.py`:

1. um juiz inline (o mesmo de `[judge]`) classifica a mensagem final do agente;
2. se o agente perguntou, o usuário simulado responde a partir do cartão `user.md` da task
   (sem `user.md`, vale um cartão padrão embutido: nada a acrescentar, siga o pedido como escrito, e "não sei"
   para o que não foi dito);
3. o agente continua a mesma sessão (`--resume`) com a resposta;
4. repete até `max_replies` respostas;
5. no fim, `check_final.py` (se houver) confere o estado e preenche os campos `*_final`
   (`check_pass_final`, `success_final`, `collateral_ok_final`).

O usuário simulado vê o pedido, o cartão e a mensagem do agente. Nunca vê condição, modelo,
`check.py` nem a resposta certa além do que o cartão diz.

| Chave de `[user_sim]` | Valores | Padrão |
|---|---|---|
| `provider` | `codex`, `anthropic` | `codex` |
| `model` | ID do provedor | o padrão do provedor (`gpt-6-luna` no Codex) |
| `reasoning_effort` | só no `codex` | do CLI |
| `max_replies` | inteiro ≥ 1 | 1 |
| `prompt_version` | `user-sim-v1` (técnico), `user-sim-lay-v1`, `user-sim-lay-v2` (leigo) | `user-sim-v1` |

Os parâmetros (`user_sim_prompt_version`, `user_sim_max_replies`, provedor e modelo) vão para o
`run_config.json`. Configs de exemplo: `configs/e13-usersim-floci.toml` (task de teste, Floci) e
`configs/e13-real-aws.toml` (casos reais, AWS real).

## Juízes

Dois juízes LLM rodam no host, depois do run, e gravam ao lado do `events.jsonl`. Nenhum deles
decide sucesso sozinho: sucesso vem do `check.py` (regra em `agentic/outcome.py`); o juiz diz se o
agente perguntou.

### Juiz de desfecho (`agent-judge`)

`agentic/judge.py`, prompt `e3-judge-v2`. Classifica a mensagem final de cada trajetória em
`ask`, `done`, `done_with_followup`, `partial` ou `gave_up`, e grava `judge.jsonl`. O provedor e o
modelo vêm de `[judge]` (`provider`, `model`); o padrão é `anthropic` com o modelo padrão do
backend (`Sonnet 5.5`).

```bash
uv run iac-harness agent-judge runs/<id>                       # julga o que falta
uv run iac-harness agent-judge runs/<id> --redo                # rejulga tudo
uv run iac-harness agent-judge runs/<id> --sample 40 --seed 0  # amostra p/ rótulo humano
uv run iac-harness agent-judge runs/<id> --agreement judge_sample.csv  # juiz × humano
uv run iac-harness agent-judge runs/<id> --provider codex --out judge-codex.jsonl
uv run iac-harness agent-judge runs/<id> --compare-with judge-codex.jsonl  # juiz × juiz
```

- **Validação contra humano:** `--sample N` grava `judge_sample.csv` (`trajectory_id`,
  `final_text_excerpt`, `judge_kind`, `human_kind`); preencha `human_kind` e rode `--agreement`.
- **Segundo juiz:** `--provider`/`--model`/`--reasoning-effort` trocam o juiz sem mexer na config;
  `--out` grava em outro arquivo; `--compare-with` dá a concordância entre os dois arquivos
  (kappa de Cohen no `kind` e no "perguntou").

### Juiz de qualidade da pergunta (`agent-judge-questions`)

`agentic/question_judge.py`, prompt `e15-question-v1`. Roda só nas trajetórias com `asked = true`
no `judge.jsonl` e grava `judge-questions.jsonl`. Vê o pedido, o `expect`, o comentário de
cabeçalho do `meta.toml` e o `user.md` (se houver), nunca condição, modelo nem id. Dá nota 0–2
em cinco critérios: C1 pertinência, C2 ganho de informação, C3 respondível por leigo,
C4 consequências de cada opção e C5 carga. O `total` é a soma, zerado quando C1 = 0.

```bash
uv run iac-harness agent-judge-questions runs/<id> [--provider codex] [--model M] [--out F] [--limit N]
uv run iac-harness agent-questions-report runs/<id>                  # notas por condição
uv run iac-harness agent-questions-report runs/<id> --compare A B    # juiz × juiz
```

O relatório sai em Markdown no stdout, sem LLM. `--compare` dá o kappa ponderado (pesos
quadráticos) por critério e grava `questions-disagreements.csv` com os desacordos de 2 pontos,
para leitura humana.

Os parâmetros de cada juiz (provedor, modelo, versão do prompt) vão em cada linha do jsonl.

## Sweep da AWS real

`executors/aws.py` (`RealAwsExecutor`) e `executors/aws_sweep.py` (roda dentro da toolchain, com
o boto3 da imagem). É o que faz uma conta AWS real servir de backend: não há "reset" na AWS, então
o harness apaga tudo o que não estava num inventário da conta limpa.

**Use uma conta dedicada.** O sweep apaga recursos de verdade.

### Como liga

```toml
[sandbox]
kind = "aws"
expected_arn = "arn:aws:iam::<conta>:user/<usuario>"   # guarda de identidade
region = "us-east-1"
# baseline_file = "runs/aws-baseline.json"            # padrão
```

- Credenciais: `IAC_AWS_ACCESS_KEY_ID` e `IAC_AWS_SECRET_ACCESS_KEY` no ambiente (a CLI não lê
  `.env`); o harness as repassa como `AWS_*` à toolchain e ao agente, só pelo nome.
- `kind = "aws"` é aceito pelos comandos de modalidade e do agente.
- No alvo aws, `agent-run` força `workers = 1` (o sweep é da conta inteira) e o agente sai pelo
  proxy `egress-proxy-aws` (abaixo).
- Use um usuário IAM com política escopada para o harness, não o root.

### Ciclo

1. **Identidade:** antes de cada trajetória do agente, `sts get-caller-identity`
   precisa devolver `expected_arn`; senão, `RuntimeError` ("identidade AWS recusada") e o comando
   para.
2. **Baseline:** na primeira chamada, lê `baseline_file`. Sem arquivo, tira um inventário da conta
   naquele momento e grava. Baseline sem alguma categoria do sweep faz o comando parar (estenda o
   arquivo à mão, com a conta limpa).
3. **Reset:** apaga tudo o que não está no baseline, na ordem de dependência, e relê o inventário
   até 10 vezes, com 10 s entre leituras (consistência eventual). Esperas de remoção (RDS, MSK,
   CloudFront) vão até 1500 s.
4. **Depois de cada trajetória:** o mesmo sweep, para a próxima começar limpa.
5. **Fim do comando:** limpeza final num `finally`, inclusive com erro.
6. Se sobrar algo fora do baseline, o comando para com "limpeza AWS incompleta" e lista o que
   sobrou. O resultado de cada limpeza sai no stderr como `aws sweep: {...}`.

O baseline representa a conta limpa e é reaproveitado entre runs e no `agent-resume`. Apagar o
arquivo faz o próximo run tirar um inventário novo: só faça isso com a conta limpa, ou as sobras
viram baseline e nunca mais são apagadas.

### Escopo

- A região de `AWS_DEFAULT_REGION` (`us-east-1`), IAM (global) e, em `us-west-2`, só EIPs,
  volumes e snapshots EC2 (usados pelos casos reais).
- Categorias (`ORDER` em `aws_sweep.py`, nesta ordem de remoção): instâncias EC2, RDS (sem
  snapshot final), MSK, Lightsail, Lambda, CloudFront, alarmes CloudWatch, usuários ElastiCache,
  DAX, human task UIs do SageMaker, Glacier, Kinesis Video, NAT gateways, EIPs, snapshots,
  volumes, ENIs, security groups, subnets, internet gateways, route tables, VPCs, subnet groups
  RDS, S3 (com versões), DynamoDB, SQS, SNS, Secrets Manager (sem recuperação), parâmetros SSM,
  chaves KMS do cliente (exclusão agendada em 7 dias), MFA virtual IAM, usuários, grupos e roles
  IAM.
- IAM só com prefixo: usuários e grupos `del-*`/`mod-*`; roles `iac-harness-*`.
- Fora, sempre: recursos default (VPC, subnets, SG, route table principal e IGW da VPC default),
  chaves KMS gerenciadas pela AWS, MFA atribuído a usuário (inclui o do root), o usuário `default`
  do ElastiCache e os grupos `default*` do DAX.

### Riscos e premissas

- Durante um run, não crie nada em `us-east-1`, nem EC2 em `us-west-2`, nem IAM com os prefixos
  acima: o que aparecer fora do baseline é apagado.
- Custa dinheiro: RDS, EC2 e sobretudo MSK cobram por hora. Ponha orçamento com alerta na conta e
  confira o Cost Explorer depois de cada run.
- Lacuna conhecida e aceita: subnets e route tables criadas pelo agente dentro da VPC default não
  são apagadas (são gratuitas).
- Serviço fora de `ORDER` não é visto: se uma task nova cria outro tipo de recurso, estenda o
  sweep (e o baseline) antes de rodar.
- Na AWS, o `check.py` reprovado depois da solução é refeito por uma janela curta (consistência
  eventual); `check_attempts` vai nos detalhes.

## Backends

| | Floci (emulador) | AWS real |
|---|---|---|
| Config | `[sandbox]` com `endpoint`, `host_endpoint`, `container`, `region` | `[sandbox] kind = "aws"`, `expected_arn`, `region` |
| Executor / alvo | `executors/floci.py`, `FlociTarget` | `executors/aws.py`, `AwsTarget` |
| Reset | `docker restart` do container + recria as VPCs default (~1 s) | sweep contra o baseline (minutos) |
| Depois da trajetória | nada (o próximo reset reinicia) | sweep |
| Workers | 1 a 4 (`[agentic] workers`) | sempre 1 |
| Rede do agente | `iac-sandbox` + `iac-egress` | `iac-egress-aws` |
| Proxy | `egress-proxy:3128` | `egress-proxy-aws`, allowlist + `.amazonaws.com` |
| Custo | nenhum além do LLM | AWS + LLM |

Os dois rodam a mesma task, o mesmo agente e o mesmo `check.py`; a concordância entre eles sai de
`agent-metrics <run-floci> --compare <run-aws>`.

### Compose (`docker/compose.yaml`)

| Serviço | Perfil | Para quê |
|---|---|---|
| `floci` | padrão | emulador, porta `127.0.0.1:4566` |
| `floci2`, `floci3`, `floci4` | `agent-workers` | um Floci por worker extra (portas 4567–4569) |
| `egress-proxy` | `agent` | saída do agente no alvo Floci |
| `egress-proxy-aws` | `agent` | saída do agente no alvo aws |

Com `workers = 4`, suba `--profile agent --profile agent-workers`.

### Proxy de egresso

`docker/egress/` (Squid com versão e digest fixados; `render.sh` monta a allowlist na subida).
As redes do agente são `internal` (sem rota para fora); só os proxies também estão na rede
`iac-egress-out`. A allowlist tem só o host de `ANTHROPIC_BASE_URL` e, no proxy aws,
`.amazonaws.com`. Antes de cada trajetória o alvo confere se o proxy responde; proxy fora do ar
para o comando em vez de virar `AGENT_ERROR` em série.

O agente roda como usuário `agent` (não root), com ferramentas fixas e sem configuração do host
(`--setting-sources ""`, `--strict-mcp-config`); detalhes em
[framework.md](framework.md#arquitetura).
