# Plano: do harness de pesquisa ao framework

Criado em 18/11/2026. Referência de desenvolvimento para transformar o `iac-agent-eval-harness`
num framework de avaliação de agentes que operam a nuvem, sem perder a reprodutibilidade dos
resultados da Residência. É um documento de trabalho: marque os itens feitos (`[x]`), registre as
decisões na seção 8 e atualize o diagnóstico quando ele mudar.

## 1. Objetivo

Apresentar, como resultado da Residência, três coisas que já existem:

1. **Os resultados** (registro metodológico, guia de decisão).
2. **O toolkit** (plugin `iac-cloud`, cada regra ligada a um achado).
3. **O framework**: o harness (agente real + backend de nuvem + verificador de estado + juízes +
   usuário simulado) e o método (achado → regra → medida, pré-registrado).

O trabalho abaixo faz o item 3 parecer e funcionar como framework: documentação de produto,
código morto removido, arquivos grandes separados, contratos explícitos e, depois, novas peças
(backends, agentes, formatos).

## 2. Restrições (valem para todas as fases)

- **Reprodutibilidade primeiro.** Os runs registrados citam comandos, configs e commits. Antes de
  qualquer refatoração, crie a tag de referência (fase 0). Depois disso, os comandos do registro
  valem para a tag, não para o `HEAD`; diga isso no README.
- **Valores gravados não mudam.** Strings que entram em `run_config.json` ou nos eventos
  (`prompt_version`, `e3-agent-v1`, `e15-question-v1`, nomes de condição como `own-rules-v2`,
  campos como `verified_state`) identificam o que foi medido. Pode renomear a constante Python,
  não o valor. Os leitores (`*-metrics`, `demo-timeline`) precisam continuar lendo os runs antigos.
- **Refatoração sem mudar o comportamento.** Cada passo das fases 2–4 mantém
  `uv run ruff check && uv run ruff format --check && uv run pytest` verde e passa no teste de
  ouro (seção 7).
- **Nada de medida nova misturada com refatoração.** Feature nova (fase 6) entra em commit
  separado, com teste próprio.
- **Sem dependência nova sem justificativa** no commit.
- **AWS real só com confirmação** (custa dinheiro e o sweep apaga recursos).
- Docs e commits em português; identificadores em inglês (convenção do repo).

## 3. Posicionamento

### 3.1 O que o harness junta e ninguém mais junta

| Diferencial | Onde está no código hoje |
|---|---|
| Agente de produção real (Claude Code headless, em container, egresso só por proxy) | `agentic/agents.py`, `docker/agent.Dockerfile`, `docker/egress/` |
| A mesma tarefa em Terraform, AWS CLI e boto3 | `modalities/` |
| Toolkit (skill, regras, plugin, MCP) como variável controlada; o plugin distribuído tem teste que garante o mesmo texto da condição medida | `agentic/conditions.py`, `toolkit/`, `tests/test_toolkit.py` |
| Concordância emulador × AWS real nas mesmas tarefas e verificadores | `agent-metrics --compare`, `executors/floci.py`, `executors/aws.py` |
| Usuário simulado para pedido ambíguo em operação de nuvem (`user.md` oculto) | `agentic/user_sim.py` |
| Juízes LLM validados (amostra humana, 2º juiz, kappa) | `agentic/judge.py`, `agentic/question_judge.py`, `agent-judge --agreement/--compare-with` |
| Reset da AWS real por inventário (baseline + sweep) | `executors/aws_sweep.py` |
| Procedência em cada run (git, imagens por digest, providers, sha da pré-validação) | `cli/provenance.py` (`_git_provenance`, `_sandbox_provenance`), `cli/agent.py` (`_agent_run_config`) |

### 3.2 Trabalhos próximos (para a tabela de posicionamento)

Links levantados em 18/11/2026 por pesquisa assistida. **Abra cada um antes de citar no TCC.**

| Trabalho | O que faz | O que falta em relação a nós |
|---|---|---|
| aws-bench ([repo](https://github.com/aws-bench/aws-bench), [anúncio](https://aws.amazon.com/about-aws/whats-new/2026/07/aws-bench/)) | Claude Code/Kiro na AWS real, verificador de estado, rollback | sem emulador, sem modalidades, sem usuário simulado |
| IaC-Eval ([repo](https://github.com/autoiac-project/iac-eval), NeurIPS 2024) | geração de HCL, intenção em Rego | geração única, sem agente operando a nuvem |
| Ambig-IaC ([arXiv 2604.02382](https://arxiv.org/abs/2604.02382)) | tarefas ambíguas, perguntas a usuário simulado | avalia o método de perguntar, offline |
| τ-bench / τ²-bench ([arXiv 2406.12045](https://arxiv.org/abs/2406.12045), [repo](https://github.com/sierra-research/tau2-bench)) | usuário simulado com instrução oculta, pass^k | domínio de atendimento, não nuvem |
| SkillsBench ([arXiv 2602.12670](https://arxiv.org/abs/2602.12670)) | efeito de skills em agentes | genérico, não nuvem |
| Harbor / Terminal-Bench 2.0 ([repo](https://github.com/harbor-framework/harbor), [tarefas](https://docs.harborframework.com/tasks/overview)) | formato de tarefa em diretório, agentes instalados, vários sandboxes | sem nuvem, sem usuário simulado |
| UK AISI Inspect ([docs](https://inspect.aisi.org.uk/)) | Task/Solver/Scorer/Sandbox, bridge para Claude Code | genérico; o METR migrou para ele |
| AIOpsLab ([repo](https://github.com/microsoft/AIOpsLab)), ITBench ([repo](https://github.com/itbench-hub/ITBench)) | agentes de SRE/AIOps em Kubernetes | operação e incidentes, não manutenção de IaC |
| HAL ([arXiv 2510.11977](https://arxiv.org/abs/2510.11977)) | leaderboard modelo × scaffold, custo × acurácia | genérico |
| ABC checklist ([arXiv 2507.02825](https://arxiv.org/abs/2507.02825)) | checklist de validade de benchmarks de agente | referência para a seção de validade |

Emuladores: Floci ([repo](https://github.com/floci-io/floci), MIT), AWS Emulator
([repo](https://github.com/aws/aws-emulator), lançado em 11/11/2026; não avalia políticas IAM,
estado só em memória) e LocalStack (Community Edition arquivada em 03/2026, token obrigatório).

## 4. Vocabulário-alvo

O framework fala o vocabulário que virou padrão (Harbor, Inspect, aws-bench). Até a fase 5, o
glossário só liga os termos aos nomes atuais; o código não muda de nome antes disso.

| Termo | Significado | Nome atual |
|---|---|---|
| Task | diretório com pedido, estado inicial e verificador | `modalities/tasks/<id>/` |
| Dataset | conjunto versionado de tasks (`iac-eval-v2`, `aws-bench`, `ambig-iac`, `real`) | prefixos `task-`, `awsb-`, `amb-`, `real-`, `lay-` |
| Backend | onde a nuvem roda: emulador ou conta real | `executors/` (feedback e modalidades), `agentic/targets.py` (agente) |
| Agent | quem resolve a task | `agentic/agents.py` (`ClaudeCodeAgent`), `agents/anthropic_backend.py` |
| Modality | forma de agir: `terraform`, `cli`, `boto3`, `free` | `modalities/spec.py` |
| Toolkit | o que se dá ao agente: nada, skill, regras, plugin, MCP | `agentic/conditions.py` (`REGISTRY`) |
| Verifier | confere o estado final sem LLM | `check.py`, `check_final.py`, `verifiers/` |
| Judge | classificação por LLM (pergunta? qualidade da pergunta) | `agentic/judge.py`, `agentic/question_judge.py` |
| User Simulator | LLM que responde a partir do `user.md` oculto | `agentic/user_sim.py` |
| Reset | volta o backend ao estado limpo | `docker restart` do Floci; `aws_sweep.py` na AWS |
| Trial | uma execução (task × rep × toolkit × modelo) | diretório `runs/<id>/<task>/<rep>/<cond>/<modelo>` |
| Run (Job) | conjunto de trials de uma config | `runs/<id>/` |
| Strata | eixos de análise (intenção, categoria, origem, dificuldade, alvo) | `agentic/strata.py`, `configs/strata.toml` |

## 5. Diagnóstico (18/11/2026; atualizado em 18/11 após a remoção do legado)

**Estado geral.** 701 testes unitários verdes (49 de integração fora do padrão); 650 (48) depois
da remoção do legado. Nenhum segredo rastreado; `runs/` e `data/` fora do git; código de terceiros
(`third_party/agent-toolkit-for-aws`) com LICENSE e NOTICE.

**Pipelines paralelos.** Eram três, as três fases da pesquisa; o de feedback no loop saiu do código
(está na tag `v1.0.0`) e ficaram dois:

| Pipeline | Entrada na CLI | Código | Situação |
|---|---|---|---|
| Feedback no loop (A/B/C) | `prevalidate`, `prevalidate-miac`, `run`, `metrics` | `runner.py`, `conditions.py`, `verifiers/opa.py` … | removido (Semanas 4–6; tag `v1.0.0`) |
| Modalidades | `modality-*`, `ambig-prevalidate` | `modalities/` | resultado da fase 2; tasks reaproveitadas pelo agente |
| Agente real | `agent-*`, `demo-timeline` | `agentic/` | **caminho principal do framework** |

Cada pipeline tem seu laço, sua gravação de eventos, sua procedência e suas métricas. Conceitos
duplicados: dois enums de término (`types.py`, que guarda os schemas gravados do legado, e
`ModTermination` em `modalities/runner.py`), três módulos de métricas (`modalities/metrics.py`,
`agentic/metrics.py`, `agentic/question_metrics.py`; o `metrics.py` ficou só com a estatística
comum), dois conceitos de backend (`executors/` e `agentic/targets.py`). O `Condition` duplicado
saiu com o `conditions.py` do legado.

**Arquivos grandes** (linhas):

| Arquivo | Linhas | Problema |
|---|---|---|
| `cli.py` | 1.750 (era 2.492) | 12 subcomandos, montagem, procedência, laços, tabelas, resume: tudo junto. Separado na fase 3 no pacote `cli/` (7 módulos, o maior com 565) |
| `executors/aws_sweep.py` | 667 | um serviço AWS atrás do outro no mesmo arquivo |
| `modalities/metrics.py` | 485 | |
| `modalities/execute.py` | 482 | |
| `agentic/metrics.py` | 465 | |
| `agentic/runner.py` | 393 | |
| `agentic/stream.py` | 337 | parser do stream + heurísticas AWS/Terraform/toolkit acopladas |

**Pontos de extensão fixos no código:**

- agente: um só (`ClaudeCodeAgent`); `AGENT_TOOLS`, `SECRET_VARS` e `GATEWAY_VARS` são do Claude
  Code/Anthropic (`agentic/agents.py`);
- LLM de texto: `if/elif` com `PROVIDERS = ("anthropic", "codex")` (`llm/__init__.py`);
- juiz: sem interface comum; `DEFAULT_JUDGE_MODEL = DEFAULT_MODEL` amarra ao Anthropic;
- toolkit: dicionário fixo (`agentic/conditions.py`);
- backend: serviços e regiões AWS fixos; proxy fixo
  em `http://egress-proxy:3128` (`agentic/targets.py`);
- tasks: as tasks são diretórios sem formato documentado (o `DatasetAdapter` sem uso saiu com o
  legado);
- prompts fixam Terraform ≥ 1.15 e `hashicorp/aws ~> 6.0` (`agents/prompts.py`,
  `modalities/prompts.py`).

**Nomes de experimento no código e nas configs:** `e3-*`, `e10-*` … `e19-*`, `own-rules-v2/v3`
(as configs `miac`, `ablation-*`, `pilot`, `screen`, `mvp` e `main-n3` saíram com o legado),
comentários "E2/E4/E13" em docstrings e seções da `cli/`. A descrição no `pyproject.toml` ainda fala de feedback em Terraform.

**Documentação:** o README é um diário de pesquisa (fases, E1–E19, datas de gate). Os 13 arquivos
de `docs/` são de pesquisa. Usuário simulado, juízes, sweep e formato de task não têm documento
próprio. Não há guia de extensão.

**Higiene:** `demo/` (847 MB com `node_modules`) só é ignorado por `.git/info/exclude`.

## 6. Fases

Ordem sugerida. As fases 0–1 não tocam código de medida e cabem a qualquer momento. As fases 2–5
são refatoração sem mudar o comportamento. A fase 6 traz features novas.

### Fase 0: base

- [x] Esperar o fim do r18 (homelab) e registrar os resultados antes de mexer em código (18/11:
  E3 real com 30 tarefas e E19 no registro).
- [x] Tag de referência dos resultados (`v1.0.0`, ver 8.1), com push (18/11, sobre `de13ca7`).
- [x] `demo/` no `.gitignore` (hoje só no `.git/info/exclude`).
- [x] Descrição do `pyproject.toml` atualizada (o que o harness avalia hoje).
- [x] Criar o teste de ouro (seção 7) antes de qualquer refatoração: `scripts/golden.sh`
  (16 casos, determinístico; 15 depois que o `metrics` do legado saiu). Snapshot gravado
  na tag (`de13ca7`, 16 casos); as branches do framework conferem 15.

### Fase 1: documentação de produto

- [x] **README com cara de produto**: o que é, para quem, diferenciais (3.1), componentes,
  quickstart de um trial no Floci, instalação do toolkit. "Estado/fases" e a história dos
  experimentos vão para `docs/pesquisa.md` (ou ficam no registro), com link.
- [x] **`docs/framework.md`**: arquitetura (diagrama dos componentes da seção 4), o ciclo achado →
  regra → medida, pré-registro, o que é medido em cada trial, posicionamento (3.2).
- [x] **`docs/tarefas.md`**: formato da task (`prompt.md`, `meta.toml`, `initial.tf`, `check.py`,
  `user.md`, `check_final.py`, `upstream/`), os prefixos por dataset, como criar e pré-validar uma
  task nova.
- [x] **`docs/componentes.md`** (ou uma seção por componente): usuário simulado (`[user_sim]`,
  `max_replies`, `success_final`), juízes (prompts versionados, `--sample`, `--agreement`,
  `--compare-with`, kappa), sweep da AWS real (baseline, escopo de regiões e prefixos, o que
  aborta, riscos).
- [x] **Mapa da CLI**: os 17 subcomandos agrupados em principal (`agent-*`), modalidades e legado.
- [x] **Limitações**: só AWS; agente só Claude Code; juiz padrão Anthropic; usuário simulado via
  Codex CLI logado; AWS Emulator não avaliado.
- [x] `configs/quickstart-floci.toml`: config neutra para o quickstart (as configs `e*` ficam
  como estão, pois o registro as cita).

### Fase 2: código morto e higiene

Confirme cada candidato com `grep` e testes antes de apagar. O vulture
(`uvx vulture src --min-confidence 60 --exclude '*/tasks/*'`) dá falsos positivos nos campos de
dataclass de `types.py`, que são serializados nos eventos: **não apague campos gravados**.

- [x] `interfaces.DatasetAdapter`: sem uso. Removido com o pipeline legado (o contrato
  `TaskSource` da fase 4 nasce do zero).
- [x] `agentic/stream.py:335` `verified_state()`: o vulture aponta sem uso (o runner usa
  `ops.verified`). Apagado; os testes que o usavam passaram a conferir `state_ops(...).verified`.
- [x] `modalities/loader.py:38`
  (`missing_reason`), `types.py:39-41` (`VALIDATE_FAIL`, `PLAN_FAIL`, `OPA_FAIL`): conferidos,
  ficam. `missing_reason` é lido do `meta.toml` (6 tasks e o adaptador do Ambig-IaC); os três
  valores de `FirstFailure` estão gravados nos `events.jsonl` dos runs do legado (comentário no
  enum).
- [x] Comandos de uso único: `modality-reask` (migração do E2) e `prevalidate-miac` removidos;
  `ambig-prevalidate` (E16) fica, porque é o gate das tasks do Ambig-IaC (ver 8.5).
- [x] Scripts de seleção (`scripts/select_*.py`, `scripts/e15_overnight.sh`): movidos para
  `scripts/experiments/`, com README dizendo qual experimento usou cada um. O
  `scripts/fetch_ambig_iac.py` fica em `scripts/` (instala o dataset Ambig-IaC).
- [x] `executors/localstack.py` e `configs/modality-spike-localstack.toml`: o LocalStack exige
  token desde 03/2026 e o spike acabou. Removidos (executor, config, serviço do compose, host da
  allowlist, testes); a tag `v1.0.0` preserva.
- [x] Toolkit v1 e v3 (`toolkit/src/`, `toolkit/src-v3/`, plugins `own-rules`, `own-rules-v3`,
  `own-tools`): ficam, porque as condições medidas os montam, mas o README deixa claro que a
  distribuída é a `iac-cloud` (v2).
- [x] Pipeline de feedback no loop: removido (comandos, runner, condições A/B/C, OPA, adaptador
  Multi-IaC-Eval, configs); a tag `v1.0.0` preserva (ver 8.2).

### Fase 3: modularizar e separar os arquivos grandes

Mover código sem mudar a lógica. Um arquivo por commit, com o teste de ouro verde.

- [x] **`cli.py` → pacote `cli/`**:
  - `cli/__init__.py`: só `main()` e o registro dos subparsers;
  - `cli/common.py`: `load_config`, `_select_modality_run`, `new_run_id`, montagem de toolchain/executor;
  - `cli/provenance.py`: `_git_provenance`, `_sandbox_provenance` e auxiliares;
  - `cli/agent.py`: `agent-run`, `agent-resume`, `agent-judge*`, `agent-questions-report`,
    `agent-metrics`;
  - `cli/modality.py`: `modality-*`, `ambig-prevalidate`;
  - `cli/tables.py`: formatação de tabelas, hoje espalhada (`_modality_table`,
    `_agent_metrics_table`, `_free_table`…);
  - `cli/demo.py`: `demo-timeline`.
  - Os laços (`_agent_loop`, `_modality_loop`, `_run_pairs`) saem da CLI e vão para os runners.
    *Não feito: ficaram em `cli/agent.py` e `cli/modality.py`. Os dois montam o contexto a partir
    da config (backends por modalidade, raiz dos plugins, estratos) e imprimem o progresso;
    levá-los para os runners muda assinaturas, não só o lugar do código. Fica para a fase 4
    (contratos). O `_run_pairs` saiu com o pipeline legado.*
- [ ] **`executors/aws_sweep.py`**: um módulo por grupo de serviço (`sweep/ec2.py`, `sweep/s3.py`,
  `sweep/iam.py`…) com um registro de sweepers. Atenção: o arquivo roda dentro da toolchain;
  conferir como ele é copiado/montado antes de virar pacote.
- [x] **`agentic/stream.py`**: separar o parser do stream do Claude Code (genérico) das
  heurísticas de classificação (`_AWS_VALUE_FLAGS`, `_TF_MUTATE`, `TOOLKIT_READ_SCRIPTS`). *O classificador foi para `agentic/state_ops.py`.*
- [ ] **Métricas**: um núcleo comum (taxas, intervalos, tokens, estratos) usado pelos quatro
  módulos; cada pipeline fica só com o que é dele.
- [ ] **`modalities/execute.py`**: separar por modalidade (terraform, cli, boto3).

### Fase 4: contratos explícitos

Trocar `if/elif` por string e dicionários fixos por interfaces (`Protocol`) com um registro
simples. Só o necessário para as peças que já existem mais as da fase 6; nada especulativo.

- [ ] `Agent`: `ClaudeCodeAgent` hoje; Codex CLI na fase 6. Variáveis de ambiente e ferramentas
  por agente, não globais.
- [ ] `Backend`: unifica `executors/` e `agentic/targets.py` (reset, endpoint, credenciais,
  procedência, limpeza final). Floci e AWS hoje; AWS Emulator na fase 6.
- [ ] `Verifier` e `Judge`: interfaces separadas (sem LLM × com LLM). Modelo e fornecedor do juiz
  vêm da config, sem default Anthropic escondido.
- [ ] `UserSimulator`: o que `user_sim.py` já faz, atrás de uma interface.
- [ ] `Toolkit`: registro carregado de diretório/config, não do dicionário em
  `agentic/conditions.py` (os nomes medidos continuam válidos).
- [ ] `TaskSource`: carrega tasks de um diretório (`modalities/tasks/` vira um dataset entre
  outros) e de adapters (IaC-Eval, Ambig-IaC).
- [ ] Unificar `Condition` (renomear a do agente para `Toolkit` resolve a colisão) e os enums de
  término.
- [ ] Tirar ids de experimento dos nomes de constante e das docstrings (os valores gravados
  ficam; ver seção 2).

### Fase 5: nomes

- [ ] Nome do pacote e da CLI (`iac_harness`/`iac-harness`) → o nome escolhido em 8.1. Manter
  `iac-harness` como alias por um tempo.
- [ ] CLI em grupos (`agent run`, `agent judge`, `modality run`…), com os nomes antigos como alias.
- [ ] `agentic/` → nome pelo papel (por exemplo `agent_eval/`), com subpacotes `agents/`,
  `backends/`, `judges/`.
- [ ] `modalities/tasks/` → `tasks/` na raiz (ou `datasets/<nome>/`), já que o agente também as usa.
- [ ] Configs: `configs/experiments/` (as `e*`, citadas pelo registro) × `configs/examples/`.
  Atualizar os caminhos citados no registro ou deixar nota.

### Fase 6: features (ordem por valor)

- [ ] **AWS Emulator como backend.** Transforma a concordância Floci × AWS em comparação de três
  backends por task e serviço. Documentar que ele não avalia IAM. Conferir se há API de reset.
- [ ] **Relatório de concordância entre backends** (por task e por serviço), a partir do
  `agent-metrics --compare` que já existe.
- [ ] **Codex CLI como segundo agente.** O provedor `codex` já existe no juiz e no usuário
  simulado; prova que o `Agent` é plugável.
- [ ] **pass^k e custo × acurácia** (τ-bench, HAL) nas métricas do agente.
- [ ] **Export para o formato Harbor** (`task.toml`, `instruction.md`, `tests/`), para rodar via
  `harbor run`. Adapter para Inspect fica opcional.
- [ ] **Checklist ABC** aplicado ao próprio harness, como seção de validade em `docs/framework.md`.
- [ ] Verificador de segurança secundário (Checkov/Trivy) para as tasks Terraform (opcional).

## 7. Como verificar uma refatoração

1. Gate do repo: `uv run ruff check && uv run ruff format --check && uv run pytest`.
2. Integração, quando a mudança tocar backend, toolchain ou agente:
   `uv run pytest -m integration` (sobe o compose; não rode com um run em andamento).
3. **Teste de ouro** (criar na fase 0): rodar os leitores sobre runs já existentes e comparar a
   saída com a da tag de referência. Exemplo:
   `agent-metrics`, `agent-questions-report`, `modality-metrics`, `metrics` e `demo-timeline` sobre
   um run de cada pipeline em `runs/` (ou um recorte copiado para `tests/fixtures/`). A saída tem
   que ser idêntica byte a byte. Implementado em `scripts/golden.sh` (`capture` grava o snapshot
   em `runs/golden/`; `check` compara). É local: depende dos runs em `runs/`, fora do git.
4. Um trial real curto no Floci (`agent-run` com uma task e um rep) antes de fechar cada fase que
   toque o caminho do agente.

## 8. Decisões em aberto

Perguntar ao usuário antes de executar o item que depende delas.

1. **Nome de venda do framework** e nome da tag de referência. O repo pode manter o nome atual.
   *Tag decidida (`v1.0.0`); nome de venda fica para depois.*
2. ~~**Destino do pipeline de feedback no loop**~~ *Decidido em 18/11: removido; a tag `v1.0.0`
   preserva (seção 9).*
3. **Quando renomear o pacote** (fase 5): antes ou depois da entrega do TCC.
4. ~~**Idioma do material externo**~~ *Decidido em 18/11 (ver seção 9).*
5. ~~**Destino dos comandos de uso único**~~ *Decidido em 18/11: `modality-reask` e
   `prevalidate-miac` saem, `ambig-prevalidate` fica (seção 9). LocalStack também decidido: sai.*

## 9. Registro de decisões

| Data | Decisão | Motivo |
|---|---|---|
| 18/11/2026 | Plano criado; fases 0–1 antes de mexer em código | não quebrar a reprodutibilidade dos runs registrados |
| 18/11/2026 | Tag de referência `v1.0.0`, sem citar gate ou semana | o nome descreve a versão, não o calendário da Residência |
| 18/11/2026 | README em inglês, com link para um resumo em português; `docs/` (incl. `framework.md`) em português | o README é a vitrine para público de fora; `docs/` é material do TCC e segue a convenção do repo |
| 18/11/2026 | Nome de venda do framework decidido depois | só trava a fase 5 |
| 18/11/2026 | LocalStack removido por completo (executor, config, compose, testes); entra depois da tag `v1.0.0` | exige token desde 03/2026, o spike acabou e o compose obrigava a exportar um token falso |
| 18/11/2026 | Pipeline de feedback no loop removido (comandos, runner, condições A/B/C, OPA, configs); entra depois da tag `v1.0.0` | a tag preserva; o código vivo fica só com modalidades e agente real |
| 18/11/2026 | `modality-reask` e `prevalidate-miac` removidos; `ambig-prevalidate` fica | uso único × gate das tasks do Ambig-IaC |
| 18/11/2026 | `cli.py` vira o pacote `cli/`; os laços de execução ficam na CLI até a fase 4 | mover sem mudar a lógica; tirar os laços da CLI exige mudar assinaturas |
