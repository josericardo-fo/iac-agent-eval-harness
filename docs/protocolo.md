# Protocolo Experimental (v3)

MVP para avaliação incremental de agentes na geração de Infrastructure as Code.
Residência em Inteligência Artificial. Revisão da v2 (30 de setembro de 2026).

Este documento é autocontido e substitui o PDF "Protocolo Experimental Inicial". As tabelas finais
resumem o que mudou.

## 1. Motivação e objetivo

A literatura analisada na Residência converge para uma cadeia progressiva de verificação:
`terraform validate` → `terraform plan` → policy/OPA → execução. Os primeiros estágios aumentam a
confiança técnica, mas não demonstram que a infraestrutura pode ser criada de fato. Zodiac mostra o
*semantic gap* entre validação de IaC e comportamento do provedor; Jouini e TerraFormer formalizam a
avaliação em estágios; Nekrasov et al. introduzem emulação no IaC-Eval; MACOG explora execução em
sandbox dentro de um loop de repair [1–5].

**Objetivo do MVP.** Construir uma base experimental reproduzível para comparar o mesmo agente sob
diferentes níveis de acesso a feedback, mantendo modelo, tarefas e critério de avaliação constantes.
O foco inicial é a geração de Terraform para AWS a partir de linguagem natural.

O IaC-Eval v2 não publica harness de avaliação; o uso adotado (plan JSON inteiro como input e
query `data.terraform.policy.main`) é deduzido do dataset card e das políticas (ver §4.5).

Duas decisões de desenho decorrem de achados da pesquisa prévia:

- Frameworks agênticos contaminam a variável experimental (prompts ocultos, retries e reprompts).
  Por isso o agente é acessado por uma interface própria (`AgentBackend`) sobre o SDK oficial, com
  loop explícito e `max_retries=0`.
- O que varia entre condições é somente o feedback permitido. O resto (modelo, prompt, parâmetros,
  avaliação final) é idêntico.

## 2. Pergunta experimental e hipótese

**Pergunta.** Qual é o ganho incremental de incorporar verificadores pré-deployment e execução
controlada ao loop de um agente de geração de IaC?

**Hipótese inicial (provisória).**

- H1: o feedback de validate/plan/OPA reduz falhas técnicas e de intenção (B > A).
- H2: a execução emulada revela uma classe adicional de falhas que passa pelos gates anteriores e,
  quando entra no repair, permite corrigir parte delas dentro de um orçamento pequeno (C > B).

O principal valor do MVP é tornar a pergunta mensurável e separar o efeito de cada estágio, não
confirmar previamente que o sandbox será melhor.

## 3. Escopo do MVP

| Decisão | Escopo inicial |
|---|---|
| Classe de tarefa | NL → Terraform (geração de IaC), greenfield. |
| Cloud / linguagem | AWS + Terraform. |
| Modelo inicial | Claude via API da Anthropic, atrás de `AgentBackend`. Padrão: Sonnet 5.5 (`claude-sonnet-5-5` na API direta; `"Sonnet 5.5"` no gateway atual); configurável e registrado por execução. |
| Dados | Todas as 186 tarefas do IaC-Eval v2, na revisão fixa descrita na §4. |
| Feedback | No máximo 3 tentativas totais nas condições com repair (geração inicial + até 2 correções). |
| Verificadores | `terraform validate`, `terraform plan` e OPA/Rego. |
| Execução controlada | Floci como emulador AWS local. |
| Repetições | N = 5 por tarefa (configurável; smoke com N = 1). |
| Persistência | Eventos e resultados em JSONL; sem banco de dados. |
| Implementação | Python 3.12, uv, pytest e ruff. Sem framework agêntico. |

Sobre estado inicial: o dataset possui o campo `has_starting_state`, que é `False` em todas as 186
tarefas. O protocolo não trata tarefas com estado inicial separadamente; o campo é apenas preservado
nos metadados.

## 4. Dados e benchmark

**Benchmark principal: IaC-Eval v2**, fixado na revisão Hugging Face
`defefc108d437f5659f40be66fc863833e9c96fb`. São 186 tarefas AWS/Terraform com políticas Rego para
intenção e saída de referência, o que permite validar o próprio harness antes de avaliar o agente
[1, 6, 9].

### 4.1. Esquema do dataset

Colunas: `task_id`, `suite`, `difficulty`, `resource`, `prompt`, `intent`, `rego_policy`,
`reference_output`, `opa_input_path`, `has_starting_state`, `source`, `terraform_version`,
`opa_version`.

- `difficulty` vai de 1 a 6, com 41/43/52/22/11/17 tarefas por nível. Todas as análises são
  estratificadas por ele.
- `prompt` já contém a seção `## Intent`. O agente recebe o campo `prompt` cru, sem reescrita.
- `opa_input_path` indica o que a política recebe: `resource_changes` (130 tarefas) ou
  `planned_values` (56). Em ambos os casos, a política recebe o **plan JSON inteiro**
  (`terraform show -json`); o campo só informa qual parte do plano a regra consulta.
- `terraform_version` é `>=1.15.0` e `opa_version` é `1.16.1`.
- Políticas em Rego v1, `package terraform.policy`, decisão apenas em `main`, booleana, sem
  `deny` nem mensagens. Todas têm regras intermediárias nomeadas, mas apenas cerca de 62 têm duas
  ou mais além de `main`/`allow`.

### 4.2. Propriedades das referências que afetam o harness

- Todas declaram `required_providers` com `aws ~> 6.0`.
- 109 declaram `provider "aws"` com `region` (2 com `alias`); 77 não declaram. O harness injeta
  o override de provider (§9), então a ausência do bloco não impede o plan/apply.
- 11 usam `archive_file` inline (provider `hashicorp/archive`) e 2 usam `random`. Esses providers
  entram no cache de plugins da toolchain.
- As tarefas `aws/task-155`, `aws/task-214` e `aws/task-215` são referências com dependência de
  arquivo local; a classificação é decidida pela pré-validação (§4.4). A 155 foi observada falhando
  só no apply (arquivo zip ausente), ou seja, fica com `sandbox_supported = false`, sem ser
  `HARNESS_INVALID`.
- Lex, Glacier, DAX e SageMaker não existem no Floci 2.1.0 (o SageMaker só entrou no `main`
  depois da 2.1.0). Essas tarefas ficam sem `sandbox_supported`.
- **Defeitos do dataset** (a política não é satisfazível pela própria referência, nem na AWS real;
  tarefas `HARNESS_INVALID` esperadas na pré-validação):
  - `aws/task-129` e `aws/task-190`: a política exige um `aws_autoscaling_group` que está comentado
    na referência, uma AMI fixa (`ami-0591e1c6a24d08458`; a referência usa `data.aws_ami`) e
    `version == "$Latest"` (a referência usa `latest_version`).
  - `aws/task-360`: a política lê `resource_changes[_].expressions`, campo que só existe em
    `configuration` no plan JSON.

### 4.3. Uso da solução de referência

A saída de referência e o Rego são **oráculo** e nunca são fornecidos ao agente. No código, ficam
separados de `Task` em `TaskOracle`, e o agente só enxerga `Task`.

### 4.4. Pré-validação do ambiente

Antes de rodar o agente, o repositório executa as referências pela cadeia completa e registra:

- se a referência passa por validate, plan e OPA;
- se pode ser aplicada no Floci e qual serviço/recurso causa incompatibilidade quando não pode;
- a cobertura do sandbox: proporção das 186 tarefas cuja referência executa no Floci.

Resultados e seus efeitos:

- **`sandbox_supported`** (nível de tarefa): verdadeiro quando a referência passa por validate, plan,
  OPA e apply. Define o denominador das métricas de sandbox.
- **`HARNESS_INVALID`**: tarefa cuja referência falha em validate, plan ou OPA (ex.: uma referência
  com dependência de arquivo local que já falhe no plan). O problema está no harness ou no dataset, não no agente. Essas tarefas são excluídas das métricas
  principais e reportadas à parte.
- Tarefa cuja referência só falha no apply do Floci continua no conjunto, sem `sandbox_supported`,
  e é reportada como limitação de cobertura do emulador (caso observado da 155).
- `run` exige `--prevalidation`; só com `--no-prevalidation` explícito (smoke) roda sem ela, assumindo
  `harness_valid` e `sandbox_supported` verdadeiros. A escolha fica em `run_config.json`.
- A pré-validação grava `prevalidation_config.json` (config e procedência, §10), e `run_config.json`
  guarda o sha256 do `prevalidation.jsonl` usado.
- **Estado inicial do sandbox.** O reset deixa o Floci como uma "conta AWS nova", igual em todas as
  condições: restart do container (~1 s), espera pelo health e criação da VPC default em todas as
  regiões comerciais padrão (§9.2). É a única intervenção no emulador.

### 4.5. Outras fontes

| Fonte | Conteúdo relevante | Uso | Decisão |
|---|---|---|---|
| IaC-Eval v2 | 186 tarefas AWS/Terraform, prompts, Rego e referências. | Benchmark principal. | USAR AGORA |
| IaC-Eval original | Benchmark original com avaliação de 20 amostras por tarefa [10]. | Referência para o desenho de amostragem. | USAR COMO REFERÊNCIA |
| Enhanced IaC-Eval | Revisão com emulação LocalStack, automação e análise de erros [7, 11]. | Referência para sandbox, orquestração e taxonomia; não conta como amostra independente. | USAR COMO REFERÊNCIA |
| TF-Gen | 152.475 instâncias; test com 704 casos. | Validação posterior em maior escala. | DEPOIS |
| TF-Mutn | 52.516 instâncias; test com 740 casos; mutação de IaC. | Muda a classe de tarefa. | FORA DO MVP |
| NSync benchmark | 372 cenários de drift em projetos reais. | Reconciliação, não geração. | FORA DO MVP |

O IaC-Eval v2 **não tem harness oficial publicado**: o dataset card cita scripts e um repositório
placeholder (`your-org/iac-eval-v2`), e o autor no Hugging Face é o mesmo de [1]. A query `main` e o
plan JSON inteiro como entrada da política são deduzidos do dataset card e das próprias políticas;
a avaliação deste harness é, portanto, uma reconstrução, não a oficial.

Enhanced IaC-Eval e IaC-Eval v2 pertencem à mesma família. Não são tratados como datasets
independentes, para não inflar o número de tarefas.

## 5. Condições experimentais

As três condições usam o mesmo dataset, modelo, prompt e configuração de geração. O que muda é o
feedback disponível na trajetória.

| Condição | Gates no loop | Orçamento | Papel |
|---|---|---|---|
| A: Agent Only | nenhum | 1 tentativa | Baseline first-shot: capacidade do modelo sem ferramentas. |
| B: Pre-deployment | validate, plan, OPA | até 3 tentativas | Ganho dos verificadores antes da execução. |
| C: Pre-deployment + Sandbox | validate, plan, OPA, sandbox | até 3 tentativas | Ganho adicional da execução controlada no loop. |

### 5.1. Desenho pareado

Para cada par (tarefa, repetição), a **tentativa 1 é gerada uma única vez** e compartilhada por A, B
e C. As condições B e C ramificam dela, cada uma com seu próprio histórico. A consequência é:

- A é, por construção, a própria tentativa 1.
- O pass@1 passa a ser uma **baseline única** (a tentativa compartilhada avaliada pela cadeia
  comum) e deixa de ser métrica de comparação entre condições.
- A comparação principal é o **final success within budget**, pareada por (tarefa, repetição).

Como o modelo não é determinístico (§7), compartilhar a tentativa 1 elimina a variância de
amostragem da primeira geração entre condições e dá poder estatístico ao pareamento.

### 5.2. Avaliação comum

As condições definem o que o agente pode usar como feedback; a avaliação final é comum. Todo
artefato final de A, B ou C é submetido externamente à mesma cadeia completa:

`validate → plan → OPA/Rego → Floci apply`

com reset do executor antes do apply. Em B, o resultado do Floci é medido ao final mas não volta
ao agente; em C, o mesmo tipo de falha pode orientar uma nova tentativa.

### 5.3. SANDBOX_UNSUPPORTED por artefato

- **Nível de tarefa:** a pré-validação da referência define `sandbox_supported` (§4.4).
- **Nível de artefato:** o `Executor` classifica o erro do apply. Erros do tipo "operação/serviço
  não implementado pelo emulador" viram `SANDBOX_UNSUPPORTED`, por uma lista de padrões versionada.
  O erro bruto é sempre registrado, para auditoria.
- Erros de infraestrutura (ex.: `no such host`) não são limitação do emulador: o status é `ERROR`.
- Na condição C, `SANDBOX_UNSUPPORTED` **não volta ao agente** como feedback. O gate é tratado como
  indisponível, sem repair, e C se comporta como B naquela trajetória. Fica registrado como
  `termination_reason = SANDBOX_UNSUPPORTED`.
- **Tarefa sem `sandbox_supported`:** o emulador não é instrumento confiável nela, então C se
  comporta como B desde a primeira tentativa (o gate SANDBOX sai do loop; o nome da condição
  continua "C"). O apply **não roda** na avaliação final dessas tarefas (evita timeouts longos,
  ex.: Redshift/MSK, cujo container nunca sobe): roda-se validate → plan → OPA, com SANDBOX `SKIPPED`. Se os três passam,
  `first_failure_stage` e `final_status` são `SANDBOX_UNSUPPORTED`; se algum falha, o `*_FAIL` normal.

- **Cobertura sem ajuste do emulador.** Não há mock de MSK nem socket Docker para Redshift. Redshift,
  MSK e `Lambda invoke` (que precisa de Docker) ficam como limitação de cobertura, reportada na
  pré-validação. O problema nesses casos é o container que não sobe, não a lentidão.

## 6. Loop de geração e repair

O loop segue o padrão verifier-first da literatura e usa a primeira falha como feedback, evitando
enviar ao modelo um conjunto excessivo de mensagens [1, 3, 4].

1. Receber a tarefa e gerar os arquivos Terraform (tentativa 1, compartilhada entre condições).
2. Executar os gates da condição, em ordem, parando na primeira falha.
3. Se houver falha (e não for `UNSUPPORTED`), normalizar o diagnóstico, truncá-lo e devolvê-lo ao
   agente.
4. Gerar nova versão, respeitando o limite de tentativas.
5. Encerrar quando todos os gates da condição passarem ou o orçamento acabar.
6. Executar a avaliação comum completa sobre o artefato final e registrar os resultados.

### 6.1. Feedback de OPA sem vazar o oráculo

O Rego não é entregue ao agente. O parâmetro `policy_visibility` controla o diagnóstico de OPA:

- `none`: apenas "falhou na verificação de intenção".
- `rule_names` (**padrão**): nomes das regras nomeadas, sem argumento, que avaliaram como falsas.
- `full`: a política completa; reservado a uma ablação futura.

Limitações declaradas:

- Só cerca de 62 das 186 políticas têm duas ou mais regras além de `main`/`allow`, então o
  diagnóstico granular é limitado nas demais.
- Jouini observou que 79% das falhas de OPA pós-repair somem quando o Rego é exposto, o que indica
  que o repair muitas vezes falha por falta de informação sobre a política, não por incapacidade do
  modelo. Isso é uma **ameaça à validade**: o ganho de B e C com `rule_names` subestima o teto
  possível com visibilidade total.

### 6.2. Taxonomia de erros

Cada falha recebe uma classe, como metadado, segundo a taxonomia de Nekrasov [3]: **Syntax**,
**Schema**, **Runtime** ou **Intent** (mais `Unknown` como fallback). A classificação é feita por
regras simples sobre a mensagem e não altera o fluxo do loop.

## 7. Amostragem e repetições

- **`temperature` é omitido** (default da API) e registrado como `null`. Modelos Claude posteriores
  ao Opus 4.6 não aceitam `temperature`, `top_p` ou `top_k` diferentes do padrão, e
  temperature 0 não é determinístico nem nos modelos que aceitam. Não há `seed` [12].
- Ficam fixos e registrados: `model_id`, `max_tokens`, `effort` (quando aplicável), `max_retries=0`
  do SDK e o `request_id` de cada chamada.
- **N = 5 repetições** por tarefa, configurável (smoke com N = 1). Jouini e Nekrasov usam
  temperatura 0 e uma execução; o IaC-Eval original usa 20 amostras; Reflexion faz média de 8
  execuções [1, 3, 10, 14]. N = 5 é um compromisso entre custo e capacidade de medir variância.
- As condições são executadas intercaladas por tarefa, para que derivas temporais do serviço
  afetem todas de modo parecido.
- O prompt é o campo `prompt` cru mais um system prompt versionado (`prompt_version`). Mudar o
  prompt gera uma nova configuração experimental.
- Todo texto enviado ao modelo fica em inglês: system prompt, template de repair e mensagens de
  feedback geradas pelo harness (OPA, truncamento). Assim ele é coerente com os `prompt` do dataset
  e com a saída do Terraform/OPA, além de comparável à literatura. Código, comentários e documentos
  seguem em português.
- O system prompt declara as versões do ambiente (Terraform >= 1.15, `hashicorp/aws ~> 6.0`,
  `archive`, `random`), as mesmas fixadas nas referências do dataset. Sem isso, a toolchain
  offline rejeita versões válidas mas ausentes do mirror (ex.: `~> 5.0`) como VALIDATE_FAIL, e o
  repair de B/C passaria a medir "adivinhar a versão do ambiente" (observado no smoke da v1 do
  prompt).
- Registrar o `model_id` devolvido pela API. Com o gateway atual o ID aceito é um alias
  ("Sonnet 5.5") e não há `request_id`; isso é uma limitação de reprodutibilidade a reportar.

## 8. Métricas e estatística

### 8.1. Métricas

| Métrica | Definição |
|---|---|
| Baseline pass@1 | Proporção de tentativas 1 (compartilhadas) que passam na avaliação comum completa. Única para as três condições. |
| Final success within budget | Sucesso final dentro do orçamento (A: 1; B/C: até 3), reportado em duas populações (abaixo). Métrica principal. |
| First-failure stage | `VALIDATE_FAIL`, `PLAN_FAIL`, `OPA_FAIL`, `SANDBOX_FAIL`, `PASS`, `SANDBOX_UNSUPPORTED` ou `HARNESS_ERROR`, na avaliação comum. O mesmo vocabulário vale para as tentativas (com `first_failure_status` FAIL/UNSUPPORTED/ERROR). |
| Sandbox-only detection rate | Entre artefatos que passam validate+plan+OPA, proporção que falha no Floci. |
| Sandbox repair rate | Em C, proporção de tentativas com falha de sandbox de status `FAIL` (não `UNSUPPORTED`) que terminaram em PASS antes do fim do orçamento. |
| Baseline pass@k | Controle exploratório: pass@k (k = 3, o orçamento de B/C) por tarefa, estimador sem viés de Chen et al. [13] sobre as tentativas 1 independentes (n = reps, c = sucessos). Separa "o feedback ajudou" de "teve mais chances". `None` com N < k. |
| Attempts to success | Número de gerações até o primeiro sucesso. |
| Tokens | Nos FinalRecords: `shared_*` (tentativa 1, compartilhada) separados de `repair_*` (reparos da condição). Por condição: compartilhada + repair daquela condição (custo marginal da condição). |
| Custo em US$ | Tokens × tabela de preço. Por condição, custo marginal (compartilhada + repair). Total do experimento: a compartilhada conta uma única vez por (tarefa, rep). Infraestrutura fica para fase posterior. |
| Classe de erro | Syntax/Schema/Runtime/Intent, como metadado. |
| Floci coverage | Percentual das 186 referências executáveis no Floci. |

Metadados registrados desde o início: duração total, duração por gate e tamanho dos diagnósticos.
As métricas de sandbox usam como denominador apenas as tarefas `sandbox_supported`. Tarefas
`HARNESS_INVALID` ficam fora das métricas principais.

**Populações.** As métricas de sucesso (`populations` no JSON) são reportadas separadamente:

- `predeploy`: todas as tarefas `harness_valid` (sem `HARNESS_INVALID`/`HARNESS_ERROR`); sucesso =
  validate, plan e OPA com PASS. Comparável à literatura.
- `end_to_end`: só as tarefas `sandbox_supported`; sucesso = `final_status == PASS` (inclui o apply).

Cada população traz baseline pass@1, taxa com Wilson, por repetição, pass rate médio por tarefa,
desvio entre repetições, fração instável, `by_difficulty`, attempts to success e as comparações
pareadas. Tokens, custo, `first_failure_stage`, `sandbox_only_detection` e `sandbox_repair_rate`
ficam fora das populações; tokens e custo somam todas as tarefas não excluídas (o custo foi gasto).

**Status de infraestrutura.** Um gate com status `ERROR` (ex.: timeout do Toolchain) é falha do
harness, não do agente. No loop, a trajetória termina com `termination_reason = GATE_ERROR`; na
avaliação final, vira `first_failure_stage = HARNESS_ERROR` e `final_status = HARNESS_ERROR`. Esses
registros ficam fora das métricas principais e são contados à parte (`harness_error`).

### 8.2. Estatística

- **Pass rate por tarefa** (c/N, com c = repetições bem-sucedidas), e média entre tarefas.
- **IC 95% por bootstrap**, reamostrando tarefas (não repetições), com seed fixo; a diferença entre
  condições é calculada de forma pareada, por tarefa.
- **McNemar exato** (binomial sobre os pares discordantes) e **intervalo de Wilson** por repetição,
  para comparabilidade com Jouini (k = 4, McNemar + Wilson) [1].
- **Holm** sobre as três comparações (A×B, B×C, A×C), aplicado dentro de cada população.
- **pass@k como controle** (secundário e exploratório): diferença pareada, por tarefa, entre B (ou
  C) e o pass@3 das tentativas 1, com bootstrap. Fica fora do Holm das três comparações principais e
  não usa McNemar, porque o pass@k não é binário por par. Leitura: na população `predeploy`, o par
  relevante é B × pass@3 (os gates do loop de B são os do critério); na `end_to_end`, C × pass@3.
  Os dois pares aparecem nas duas populações.
- **Variabilidade:** desvio padrão entre repetições e fração de tarefas instáveis (0 < c < N).
- Tudo **estratificado por `difficulty`** (1–6), inclusive o baseline pass@1.

## 9. Organização do repositório

**Princípio.** A modularidade vem de contratos pequenos, não de muitas camadas. Cada componente
provavelmente substituível recebe uma interface simples; o MVP implementa uma única opção de cada.

| Componente | Implementação inicial | Extensões futuras |
|---|---|---|
| `AgentBackend` | Anthropic/Claude | modelos locais, OpenAI, Gemini |
| `DatasetAdapter` | IaC-Eval v2 | TF-Gen, TF-Mutn, NSync, datasets próprios |
| `Verifier` | validate, plan, OPA e sandbox (via `Executor`) | Checkov, TFLint, semantic checks |
| `Executor` | Floci | LocalStack, conta efêmera, outros emuladores |
| `Recorder` | JSONL | SQLite, Parquet, banco |

Sem registry de plugins, DAG, event bus ou framework de agentes: interfaces Python pequenas, um
runner explícito e um dicionário simples de nome → classe na CLI. Um verificador novo é adicionado
às condições e à cadeia final por configuração.

### 9.1. Contratos mínimos

- `AgentBackend.generate(task, history)`: recebe a tarefa e o histórico de feedback permitido e
  retorna artefato, uso de tokens, `model_id`, `request_id`, texto bruto e parâmetros efetivos. O
  runner não importa o SDK da Anthropic.
- `DatasetAdapter.load()`: produz `(Task, TaskOracle)` de forma uniforme.
- `Verifier.check(workspace, oracle)`: retorna `CheckResult` com estágio, status, mensagem
  normalizada, saída bruta e metadados.
- `Executor.apply(workspace)` / `reset()`: aplica em ambiente controlado, oferece reset e nunca
  aponta silenciosamente para a nuvem real. No loop, é exposto como `Verifier` via `SandboxGate`.
- `Recorder.record(event)`: persiste cada tentativa e o resultado final, sem esconder eventos
  intermediários.

### 9.2. Segurança por construção

- O harness injeta `zz_harness_override.tf` com um provider `aws` cujos endpoints apontam todos
  para o Floci (porta 4566), credenciais fictícias, `skip_*`. O override não define `region` de propósito, para não sobrescrever a região declarada
  pelo artefato; sem região declarada, vale o `AWS_REGION` da toolchain. Isso cobre também
  providers com `alias` (localizados contando chaves, não por regex).
- terraform/opa rodam com um env montado do zero (sem herdar `os.environ`): `AWS_ACCESS_KEY_ID=test`,
  `AWS_SECRET_ACCESS_KEY=test`, `AWS_REGION`, `AWS_ENDPOINT_URL=<endpoint do Floci>`,
  `AWS_EC2_METADATA_DISABLED=true`, `TF_IN_AUTOMATION=1`. No docker vão por `-e`; nunca se repassa
  `AWS_PROFILE`, `AWS_SESSION_TOKEN` nem credenciais do host. O endpoint passa pela allowlist em
  todos os modos. O override continua como defesa em profundidade.
- `plan` e `apply` rodam em container, numa rede Docker `internal` compartilhada somente com o
  Floci. `plan` também passa pelo Floci, não só `apply`.
- O executor recusa qualquer endpoint fora da allowlist.
- Chaves da Anthropic entram por variável de ambiente ou `.env` ignorado pelo Git.
- O estado do sandbox é resetado entre tarefas e antes da avaliação final por `docker restart` do
  container do Floci, espera por `GET /_floci/health` e criação da VPC default em cada região
  (`DescribeVpcs`). Motivo, confirmado no Floci 2.1.0: `POST /_floci/state/reset` limpa S3 e
  DynamoDB, mas apaga a VPC default e as subnets default e não as recria; além disso, a VPC default
  de uma região só nasce na primeira chamada EC2 a ela, o que tornava o resultado dependente da
  ordem das chamadas (17 referências de RDS falhavam, e a 218 passou 1 vez e falhou 3). Falha de
  reset aborta o comando: não vira status.

## 10. Reprodutibilidade

Fixar e registrar:

- a revisão HF do dataset;
- o Terraform (1.15.x exato) e o OPA 1.16.1, numa imagem de toolchain;
- as versões exatas dos providers (`aws` 6.66.0, `archive` 2.8.1, `random` 3.9.1), fixadas no
  Dockerfile da toolchain, e o `.terraform.lock.hcl` gravado por tentativa;
- o digest da imagem do Floci, fixado no `compose.yaml`;
- o commit git e se a árvore estava suja, o ID da imagem da toolchain, a lista de providers do
  mirror, a imagem e o digest do Floci em execução, `UNSUPPORTED_PATTERNS_VERSION` e o sha256 da
  pré-validação, em `run_config.json` e `prevalidation_config.json`;
- provider, `model_id` e parâmetros de geração (`temperature = null`, `max_tokens`, `effort`);
- `prompt_version` e o orçamento de tentativas.

Todo Terraform produzido fica em um diretório por tentativa
(`runs/<run_id>/<task>/<rep>/<cond>/attempt_<i>/`) e a trajetória anterior nunca é sobrescrita.

## 11. Schema mínimo do JSONL

Há um evento por tentativa e um por resultado final. Cada linha permite reconstruir a trajetória
sem logs soltos. Campos mínimos:

- `run_id`, `task_id`, `condition`, `rep`, `dataset_version`;
- `model_provider`, `model_id`, `params` (inclui `temperature: null`), `request_id`;
- `attempt_index` e `termination_reason`;
- status de `validate`, `plan`, `opa` e `sandbox` (PASS, FAIL, UNSUPPORTED, SKIPPED, ERROR);
- `first_failure_stage`, diagnóstico normalizado e classe de erro;
- tentativas: `input_tokens`, `output_tokens`, `total_tokens`, `first_failure_status`,
  `feedback_sent` (texto exato enviado na próxima tentativa, ou null), `durations` por estágio,
  `diagnostic_chars` e `response_path` (`response.txt` no workspace, com a resposta bruta);
- finais: `shared_input_tokens`/`shared_output_tokens` e `repair_input_tokens`/`repair_output_tokens`;
- caminho e hash (sha256) do Terraform produzido;
- `final_status`: `PASS`, `FAIL`, `SANDBOX_UNSUPPORTED`, `HARNESS_INVALID` ou `HARNESS_ERROR`.

## 12. Ordem de implementação

1. Ingestão do IaC-Eval v2 (revisão fixa) e representação `Task`/`TaskOracle`.
2. Verificadores independentes: validate, plan e OPA.
3. Executor Floci, override de segurança e reset.
4. Pré-validação das 186 referências e relatório de cobertura (`sandbox_supported`,
   `HARNESS_INVALID`).
5. `AgentBackend` e implementação Anthropic/Claude.
6. Runner de uma tarefa para A, B e C, com tentativa 1 compartilhada.
7. Recorder JSONL e métricas com estatística.
8. Smoke (N = 1) e, estável, execução completa (N = 5) nas 186 tarefas.

## 13. Critérios de conclusão do MVP

- Carregar e identificar as 186 tarefas na revisão fixa.
- Executar as referências pela cadeia e produzir a cobertura do Floci e a lista de `HARNESS_INVALID`.
- Executar uma mesma tarefa em A, B e C sem alterar o código do runner, com tentativa 1 compartilhada.
- Usar Claude via API sem acoplar o runner ao SDK.
- Aplicar o limite de 3 tentativas em B e C.
- Não enviar `SANDBOX_UNSUPPORTED` como feedback.
- Produzir JSONL suficiente para recalcular todas as métricas e estatísticas sem logs textuais.
- Garantir que nenhum apply possa atingir a AWS real por configuração acidental.
- Manter testes mínimos das interfaces e lint/format via ruff.

## 14. Fora do escopo e extensões

- Checkov e TFLint como gates ativos (a interface permite adicioná-los depois);
- RAG, MCP, otimização de prompt, memória, multiagente e fine-tuning;
- custo da infraestrutura, quotas e capacidade;
- deployment em conta AWS real e comparação sistemática Floci × nuvem real;
- multi-cloud;
- tarefas de mutation e reconciliation (TF-Mutn e NSync);
- ablação de `policy_visibility = full`;
- dashboard, banco de dados, paralelismo distribuído e CI complexo.

**Próxima expansão científica.** Avaliar a fidelidade do Floci em uma amostra de nuvem real e medir
falsos "pass" e "fail" do emulador. A segunda expansão é mutation, sem misturar essa classe com
geração greenfield no mesmo indicador agregado.

### 14.1. Ameaças à validade

- **Emulador × AWS real.** O plan e o apply rodam contra o Floci, não contra a conta AWS dos autores
  do dataset. A política pode ser satisfeita (ou não) por diferenças do emulador.
- **Nenhum harness da literatura faz apply.** A população `predeploy` é a única comparável; a
  `end_to_end` é específica deste harness.
- **Contaminação de treino.** O dataset é público desde 2024 e pode estar nos dados do modelo.
- **`rule_names`.** O diagnóstico de OPA é limitado (§6.1); o ganho de B e C subestima o teto.
- **Estado do Floci dentro de uma cadeia.** O restart só acontece antes do apply, então o estado
  deixado pelo apply de uma cadeia pode afetar o plan da seguinte. Risco baixo: os workspaces não
  têm `tfstate`.

## 15. Referências

[1] Jouini, M. (2026). Verifier-First Evaluation of Agentic LLMs for Infrastructure-as-Code
Generation. https://arxiv.org/abs/2607.20478

[2] Qiu, Y. et al. (2024). Unearthing Semantic Checks for Cloud Infrastructure-as-Code Programs.
SOSP 2024. https://doi.org/10.1145/3694715.3695974

[3] Nekrasov, R. et al. (2026). IaC Generation with LLMs: An Error Taxonomy and A Study on
Configuration Knowledge Injection. ACM TOSEM. https://doi.org/10.1145/3817608

[4] Jana, P. et al. (2026). TerraFormer: Automated Infrastructure-as-Code with LLMs Fine-Tuned via
Policy-Guided Verifier Feedback. ICSE-SEIP 2026. https://doi.org/10.1145/3786583.3786898

[5] Khan, R. N. H. et al. (2026). From Intent to Deploy / MACOG. Literatura de referência da Base
da Residência.

[6] IaC-Eval v2, dataset público. https://huggingface.co/datasets/iac-eval-v2/iac-eval-v2
(revisão `defefc108d437f5659f40be66fc863833e9c96fb`)

[7] Nekrasov et al. Pacote de replicação do Enhanced IaC-Eval. https://zenodo.org/records/20272829

[8] Floci, AWS local emulator. https://github.com/floci-io/floci

[9] Base interna: Base Literatura Residência e Síntese Comparativa da Literatura (Semana 3).

[10] IaC-Eval original. https://github.com/autoiac-project/iac-eval

[11] Enhanced IaC-Eval. https://arxiv.org/abs/2512.14792

[12] Anthropic. Messages API, parâmetros de amostragem (temperature).
https://platform.claude.com/docs/en/api/messages

[13] Chen, M. et al. (2021). Evaluating Large Language Models Trained on Code (Codex, pass@k).
https://arxiv.org/abs/2107.03374

[14] Shinn, N. et al. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning.
https://arxiv.org/abs/2303.11366

Nota: os números de TF-Gen, TF-Mutn e demais métricas de escopo foram consolidados da base interna
[9]; páginas públicas foram usadas apenas para verificar a disponibilidade atual de IaC-Eval v2,
Enhanced IaC-Eval e Floci.

## Mudanças em relação à v2 (30 de setembro de 2026)

| # | Tema | v2 | v3 |
|---|---|---|---|
| 1 | Reset do sandbox (§4.4, §9.2) | `POST /_floci/state/reset`, "efeito a confirmar". | Restart do container + VPC default em todas as regiões. O reset nativo apaga a VPC default (confirmado no Floci 2.1.0). Falha aborta. |
| 2 | Defeitos do dataset (§4.2) | Não previstos. | Tarefas 129, 190 e 360 têm política insatisfazível pela própria referência; esperadas como `HARNESS_INVALID`. |
| 3 | Harness oficial (§4.5) | Não mencionado. | Não existe harness oficial do v2; `main` e o plan JSON inteiro são deduzidos do dataset card e das políticas. |
| 4 | Cobertura do emulador (§5.3) | Exemplo "Redshift/MSK" por lentidão. | Sem ajuste do emulador (sem mock de MSK nem socket Docker); Redshift, MSK e Lambda invoke são limitação. O problema é o container que não sobe. |
| 5 | Controle pass@k (§8.1, §8.2) | Só B e C contra A. | pass@3 das tentativas 1 como controle de "mais tentativas sem feedback"; exploratório, fora do Holm, sem McNemar. |
| 6 | Procedência (§10) | Digest do Floci e lock de providers previstos. | Digest e providers fixados, e git, imagens, mirror e sha256 da pré-validação registrados; a pré-validação também grava `prevalidation_config.json`. |
| 7 | Ameaças à validade (§14.1) | Só a limitação de `rule_names` (§6.1). | Nova subseção: plan no emulador, ausência de apply na literatura, contaminação, `rule_names` e estado do Floci. |

## Mudanças em relação à v1 (Semana 4)

| # | Tema | v1 | v2 |
|---|---|---|---|
| 1 | Desenho | Condições independentes; pass@1 comparado entre A, B e C. | Tentativa 1 compartilhada por (tarefa, rep); pass@1 é baseline única; comparação principal é final success within budget. |
| 2 | Amostragem | Parâmetros de geração "fixos" sem detalhe; uma execução. | `temperature` omitido e registrado `null`; N = 5 repetições; `max_retries=0`; `request_id` registrado. |
| 3 | Estatística | Apenas proporções. | Pass rate por tarefa, bootstrap pareado por tarefa, McNemar exato, Wilson, Holm, instabilidade, estratificação por `difficulty`. |
| 4 | Sandbox sem suporte | `SANDBOX_UNSUPPORTED` só por tarefa. | Também por artefato (padrões versionados); nunca vira feedback; C se comporta como B nessa trajetória. |
| 5 | Referência inválida | Não previsto. | `HARNESS_INVALID` (referência falha em validate/plan/OPA), fora das métricas principais. |
| 6 | Feedback de OPA | "Regra/condição violada" sem definição. | `policy_visibility` (`none`, `rule_names`, `full`); limitações e ameaça à validade (Jouini). |
| 7 | Segurança | Endpoint local e credenciais fictícias. | Override de provider injetado, rede Docker `internal`, allowlist de endpoints, plan também via Floci. |
| 8 | Reprodutibilidade | Versões de dataset, Terraform, OPA e Floci. | Revisão HF fixa, Terraform 1.15.x, OPA 1.16.1, lock de providers, digest do Floci, `prompt_version`. |
| 9 | Métricas | Conjunto da v1. | Acrescenta custo em US$ e classe de erro (taxonomia de Nekrasov). |
| 10 | Estado inicial | Reportado separadamente. | Removido; o campo `has_starting_state` é `False` em todas as tarefas. |
| 11 | Modelo | "Claude", sem versão. | Padrão `claude-sonnet-5-5`. |
| 12 | Referências | [1]–[8]. | Acrescenta IaC-Eval original, Enhanced IaC-Eval, docs da Anthropic, Codex e Reflexion. |
| 13 | Confiabilidade do emulador | Só a regex sobre a mensagem de erro do Floci. | Critério empírico: se a referência roda no Floci (`sandbox_supported`). Sem isso, C vira B, o apply não roda na final e as métricas se dividem em pré-deploy e ponta a ponta. |
