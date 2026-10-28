# Seleção das tarefas do experimento de modalidades

Critério fixado antes de escrever qualquer tarefa nova. O resultado do sorteio fica em
`src/iac_harness/modalities/selection.json`, gerado por
`uv run python scripts/select_modality_tasks.py` (determinístico).

## Por que ampliar

As 5 tarefas de criação atuais (aws/task-108, 115, 230, 231, 234) são todas de dificuldade 1–2
e cobrem só S3, DynamoDB e VPC. Com 8 tarefas no total, a comparação entre modalidades fica
frágil e não diz nada sobre tarefas mais difíceis. Entram mais 7 de criação, do IaC-Eval v2, e
mais 5 de modificação, autorais.

## Tarefas de criação: sorteio

Pool: as 76 tarefas do IaC-Eval v2 executáveis no Floci (`runs/prevalidation-merged`, linhas com
`sandbox_supported` e `harness_valid` verdadeiros). Regras, nesta ordem:

a) Excluir as 5 já usadas: aws/task-108, 115, 230, 231, 234.

b) Excluir tarefas cuja `reference_output` declare `data "archive_file"` ou
   `resource "aws_lambda_function"`: o zip não tem equivalente simétrico no CLI/boto3 sem
   ferramentas extras.

c) Excluir tarefas sem nenhum bloco `resource` (só data source).

g) Excluir enunciados (sem o `## Intent`) que citam "terraform" ou "HCL". Pedir "a Terraform
   script" não é neutro entre modalidades, e o enunciado não pode ser editado. É o mesmo critério
   usado nas 5 tarefas já escolhidas. Acrescentada na v2, antes de qualquer tarefa nova rodar.

d) Serviço da tarefa = segmento após `aws_` até o próximo `_` do primeiro `resource "<tipo>"` da
   `reference_output` (ex.: `aws_s3_bucket` → s3, `aws_lightsail_instance` → lightsail). No máximo
   2 tarefas por serviço **entre as sorteadas** (as 5 já usadas não contam).
   - Revisão da regra (v2, 20/10/2026, antes de qualquer tarefa nova ser escrita ou rodada): na v1
     o teto contava as 5 já usadas, e o estrato 5–6 ficava vazio (15 das 16 candidatas são vpc e
     1 é s3, todas no teto). A v2 corrige isso sem olhar resultado de agente.

e) Estratos por `difficulty` do CSV: 1–2 → 1 tarefa, 3 → 2, 4 → 2, 5–6 → 2.

f) Semente `20261020`. Para cada estrato, na ordem acima: ordenar os ids do estrato, embaralhar
   com `random.Random(f"{SEED}-{estrato}")` e percorrer pegando as tarefas que não estouram o teto
   de serviço (teto global, atualizado a cada escolha) até completar a cota. O restante, na ordem
   embaralhada, é a fila de substitutos do estrato.

## Gate

Cada tarefa sorteada passa pelo `modality-prevalidate`. Ela só entra se no Floci der `011` nas 3
modalidades. Se falhar, é substituída pela próxima da fila do mesmo estrato (respeitando o teto
de serviço), e o motivo fica registrado. Nunca há escolha à mão.

## Enunciado

O enunciado é o `prompt` do CSV na revisão fixada (`defefc1`), sem o bloco `## Intent`, byte a
byte, sem nenhuma outra edição: não alteramos instâncias do dataset. O teste
`tests/test_modality_task_prompts.py` confere isso para toda tarefa com
`origin = "iac-eval-v2:..."`. Enunciados ambíguos entram assim mesmo e ficam marcados no
`meta.toml` (como a task-230).

## Tarefas de modificação

As 3 atuais continuam. As 5 novas são escritas por nós, no estilo do paper, cobrindo serviços
diferentes. Não vêm de dataset e ficam com `origin = "authored"`.

## Resultado do gate (20/10/2026)

As 7 sorteadas deram `011` no Floci nas 3 modalidades, então nenhuma precisou de substituição.

- **Referências.** O enunciado nunca muda. A referência Terraform do CSV só é adaptada quando não
  realiza o que o enunciado pede, e o motivo fica no `meta.toml`. Único caso: task-110, em que
  `bucket_prefix` virou `bucket = "wellcomecollection-platform-infra"`, porque o enunciado nomeia
  o bucket. O agente nunca vê a referência.
- **LocalStack** (só referências, para checar se o resultado depende do emulador):
  - task-130 (RDS) e task-201 (MSK) ficam fora da licença Hobby;
  - task-373 e task-375 (Lightsail) não têm suporte;
  - task-108 diverge nas 3 modalidades: o LocalStack confere `expected_bucket_owner`, como a AWS
    real faz, e o Floci não.
- **Achados sobre o dataset:**
  - task-233 ("data encryption at rest"): a AWS criptografa toda tabela por padrão, então a
    tarefa equivale a "crie uma tabela";
  - task-230: não diz qual tabela (ver `desenho-modalidades.md`).

## Tarefas só na AWS real

Na AWS real, as 25 tarefas do emulador só repetem o que o Floci já mostrou. Por isso entram 7
tarefas de criação do IaC-Eval v2 que **não rodam no Floci**, sorteadas com critério fixo. O
resultado fica em `src/iac_harness/modalities/selection_real_aws.json`, gerado por
`uv run python scripts/select_real_aws_tasks.py` (determinístico).

### Critério

Pool de partida: as 107 tarefas de `runs/prevalidation-merged` com `harness_valid` verdadeiro e
`sandbox_supported` falso. Filtros de custo e de neutralidade, nesta ordem (sobram 37):

- 8 excluídas porque o enunciado (sem o `## Intent`) cita "terraform" ou "HCL";
- 3 excluídas por zip/Lambda (`data "archive_file"`, `aws_lambda_function` ou `filename =`);
- 48 excluídas por serviço caro ou fora da lista: algum `resource` fora dos tipos gratuitos
  enquanto parados (DAX/ElastiCache sem cluster, Glacier, SNS, SageMaker HumanTaskUI, VPC etc.)
  ou cobrados por hora (RDS, Lightsail DB);
- 7 excluídas por classe grande: RDS/Lightsail DB fora de `db.t3.micro`, `db.t4g.micro` e dos
  bundles micro/nano;
- 4 excluídas porque citam outra região (tabelas globais do DynamoDB ficam).

### Sorteio

- Só tarefas sem região ou com us-east-1 (32 das 37).
- Serviço = primeiro tipo, em ordem alfabética, fora dos auxiliares (VPC, subnet, IAM, KMS, SNS,
  alarme); no máximo 1 tarefa por serviço entre as sorteadas.
- Estratos por `difficulty`: 1 → 3 tarefas, 2 → 3, ≥ 3 → 2.
- Semente `20261028`. Para cada estrato, na ordem acima: ordenar os ids, embaralhar com
  `random.Random(f"{SEED}-{estrato}")` e pegar as de serviço ainda não usado até a cota. O resto,
  na ordem embaralhada, é a fila de substitutos.

Resultado: task-367 (Lightsail DB), task-183 (SageMaker HumanTaskUI) e task-416 (RDS io1) no
estrato 1; task-388 (Glacier + SNS), task-198 (ElastiCache user group) e task-228 (DAX parameter
group) no estrato 2; task-229 (DAX subnet group) no estrato 3. O estrato 3 rende só 1 das 2: as
outras candidatas dele são de serviços já sorteados. Por isso são 7, e não 8.
Depois do gate, task-183 e task-388 foram substituídas por task-267 e task-069 (ver "Resultado
do gate (28/10/2026)").

### Gate e substituição

Cada tarefa passa pelo `modality-prevalidate` na AWS real: o baseline (conta limpa) reprova e a
referência passa em cada forma. Se uma tarefa falhar no gate (ex.: API descontinuada), entra a
próxima da fila do mesmo estrato que não repita serviço, e o motivo fica registrado. Nunca há
escolha à mão.

O enunciado segue a mesma regra das outras tarefas do IaC-Eval: byte a byte, sem o `## Intent`.
As adaptações das referências ficam no `meta.toml` de cada tarefa.

### Resultado do gate (28/10/2026)

`modality-prevalidate` na AWS real (`runs/20261028T012801-modality-prevalidate`; a task-228 veio
do smoke `runs/20261028T012546-modality-prevalidate`). "Válida" = baseline reprova e referência
passa.

| Tarefa | Terraform | CLI | boto3 | Resultado |
|---|---|---|---|---|
| task-367 (Lightsail DB) | falha | falha | falha | bundle `micro_1_0` descontinuado; referência adaptada |
| task-183 (SageMaker HumanTaskUI) | falha | falha | falha | serviço fechado a contas novas; substituída |
| task-416 (RDS io1) | válida | válida | válida | |
| task-388 (Glacier + SNS) | falha | falha | falha | API fechada a contas novas; substituída |
| task-198 (ElastiCache user group) | válida | válida | válida | |
| task-228 (DAX parameter group) | válida | válida | válida | |
| task-229 (DAX subnet group) | válida | válida | válida | |
| 5 do aws-bench | válida¹ | válida | válida | |
| task-267 (IAM virtual MFA device)² | válida | válida | válida | substituta da task-183 |
| task-069 (Kinesis Video stream)² | válida | válida | válida | substituta da task-388 |
| task-367, referência adaptada³ | válida | válida | válida | |

¹ Só `create-s3` e `lambda-versioned-alias-update` têm referência Terraform
(`docs/datasets.md`).
² Rerun na AWS real: `runs/20261028T024500-modality-prevalidate`.
³ Em `runs/20261028T024500-modality-prevalidate`, a CLI estourou o timeout porque o Mac dormiu
durante o run (não é erro do script); o rerun `runs/20261028T125920-modality-prevalidate` passou
nas três formas.

Achados (a AWS mudou depois que o dataset foi feito):

- **task-183:** `CreateHumanTaskUi` responde "Amazon Augmented AI is no longer accepting new
  customers." O Amazon A2I não aceita clientes novos.
- **task-388:** `CreateVault` responde `NoLongerSupportedException` ("This API is no longer
  supported for new accounts. Please use S3 Glacier storage classes instead."). Vaults do
  Glacier não existem mais para contas novas.
- **task-367:** `CreateRelationalDatabase` recusa o bundle `micro_1_0` ("invalid or
  deprecated"), e o blueprint `mysql_8_0` também saiu. O enunciado não cita versão nem tamanho,
  então a tarefa fica e a referência passa a usar as opções ativas em us-east-1: `micro_2_0` (o
  mais barato, US$ 15/mês) e `mysql_8_4` (o único mysql). O motivo está no `meta.toml`.

Substituições, pela regra acima (registradas em `replacements` no
`selection_real_aws.json`; o sorteio não muda):

- **Estrato 1:** task-183 → **task-267** (IAM virtual MFA device). A primeira da fila, task-368,
  é Lightsail DB, serviço da task-367, que continua.
- **Estrato 2:** task-388 → **task-069** (Kinesis Video stream). A primeira da fila, task-370,
  também é Lightsail DB.

As duas substitutas passaram no gate nas três modalidades (tabela acima).

As 5 tarefas do aws-bench que completam o conjunto só na AWS real estão documentadas em
`docs/datasets.md` (a sexta, `invalidate-cloudfront-cache`, está fora até decidir como passar o
domínio e o id da distribuição, que só existem depois do initial).
