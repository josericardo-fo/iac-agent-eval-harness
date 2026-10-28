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

### Agente na AWS real (28/10/2026)

`modality-run` com Haiku 5.5, N = 1, nas 12 tarefas × 3 formas
(`runs/20261028T133701-modality-real-aws-only`; caiu uma vez na limpeza da task-198 e foi
retomado com `--resume`). Inventário do root ao final: nada fora do baseline.

| Forma | Criar (8) | Modificar (4) | Total | Tokens médios |
|---|---|---|---|---|
| Terraform | 7 | 2 | 9/12 | 4,8k |
| CLI | 6 | 2 | 8/12 | 6,2k |
| boto3 | 5 | 3 | 8/12 | 5,5k |

Nenhuma diferença pareada é significativa (McNemar + Holm, p = 1,0). Sem a task-416, que
falhou igual nas três formas por bloqueio de permissão, o total fica 9/11, 8/11 e 8/11: a
conclusão não muda (a tarefa não entra nos pares discordantes do McNemar). Decisão (28/10): a
task-416 fica no conjunto, classificada como **bloqueio de permissão**, separada dos erros do
agente; liberar uma classe maior afrouxaria a trava de custo só para fazer passar.

Falhas por origem:

- **task-416 (3 formas), bloqueio de permissão (não conta como erro do agente):** o agente escolheu `db.m5.large` para o io1 e a
  política `iac-harness-scoped-v2` (só RDS micro) negou `CreateDBInstance`; as 3 tentativas
  foram gastas pedindo a liberação. A referência passa porque usa `db.t3.micro`. Achado: a trava
  de custo restringe o espaço de soluções do agente.
- **task-198 (CLI e boto3), agente:** o Redis exige um usuário com nome `default` no grupo
  (`DefaultUserRequired`). O Terraform corrigiu na 2ª tentativa trocando um dos 3 usuários; CLI
  e boto3 acrescentaram o `default` como 4º membro e o grupo ficou com 4 usuários, não 3.
- **task-367 (boto3), agente:** `KeyError` ao ler o blueprint escolhido pela API.
- **awsb-move-s3 (Terraform e CLI), enunciado:** o caminho dado está errado de propósito; o
  agente não descobriu o certo.
- **awsb-lambda-alias (Terraform) e awsb-dynamodb-strip (CLI), agente:** solução executou, mas o
  estado ficou errado (falha silenciosa).

`local-exec`: o Terraform o usou em 3 das 5 tarefas do aws-bench (dynamodb-strip, move-s3,
sync-s3), as que não têm recurso nativo; ali o Terraform resolve chamando a CLI por baixo.

## Falhas por origem no Floci (28/10/2026)

Cada execução reprovada dos dois runs no emulador, classificada pela mesma regra da AWS real.
A origem vem da leitura do artefato e da saída de cada tentativa (`events.jsonl`, evento
`attempt`). Quando a leitura não basta para separar duas origens, a linha leva as duas.

### Haiku (`runs/20261027T150905-modality-haiku`, N = 3)

| Origem | Terraform | CLI | boto3 |
|---|---|---|---|
| agente | 1 | 0 | 0 |
| agente/enunciado | 0 | 0 | 2 |
| agente/emulador | 1 | 0 | 0 |
| enunciado | 0 | 6 | 6 |
| emulador | 1 | 0 | 0 |
| harness | 0 | 1 | 0 |
| **Total de falhas (de 75)** | **3** | **7** | **8** |

- **del-s3-bucket, Terraform rep 1, agente.** O agente pôs `force_destroy = true` no bucket
  `del-logs-old` e deixou o recurso na configuração. O apply só apagou objetos e versionamento;
  o bucket ficou (falha silenciosa). Artefato:
  `del-s3-bucket/1/terraform/attempt_1/main.tf`.
- **task-115, CLI e boto3, reps 1–3, enunciado.** As 6 execuções procuram o bucket
  `pike-680235478471`, não acham e param sem criar ("não existe ou não está acessível"). O
  Terraform cria o bucket e passa. Saídas em `task-115/<rep>/{cli,boto3}/attempt_*`.
- **task-230, CLI e boto3, reps 1–3, enunciado.** "the DynamoDB table" não existe; os scripts
  pedem o nome da tabela (`TABLE_NAME`) ou dizem "nenhuma tabela encontrada". Saídas em
  `task-230/<rep>/{cli,boto3}/attempt_*`.
- **task-231, boto3 rep 3, agente/enunciado.** Mesmo padrão da task-230: o script exige
  `--table` e depois diz que não há tabela. Mas o enunciado não diz "a tabela", e as outras
  8 execuções da tarefa criaram uma e passaram. Artefato: `task-231/3/boto3/attempt_*`.
- **task-373, boto3 rep 3, agente/enunciado.** O script supõe que a instância
  `app-instance-01` já existe e para quando não a acha. "attaches ... to a Lightsail Instance"
  admite as duas leituras; as outras 8 execuções criaram a instância. Artefato:
  `task-373/3/boto3/attempt_*`.
- **query-sg-ssh-open, Terraform rep 3, emulador.** O código usa os filtros `ip-permission.*`,
  que o Floci ignora (`desenho-modalidades.md` §7); a resposta lista os 5 grupos. Artefato:
  `query-sg-ssh-open/3/terraform/attempt_2/main.tf`.
- **query-sg-ssh-open, Terraform rep 2, agente/emulador.** O código filtra as regras com
  `is-egress = false` e não confere `is_egress` em cada regra. O Floci ignorou o filtro, as
  regras de saída (`-1`, `0.0.0.0/0`) entraram e a resposta listou os 5 grupos. Só que
  `is-egress` não está entre os filtros documentados de `DescribeSecurityGroupRules` (só
  `group-id`, `security-group-rule-id` e `tag:`). Na AWS real, o mesmo código pode falhar ou dar
  erro; o run `modality-real-aws-agent`, que tem a tarefa, decide. Artefato:
  `query-sg-ssh-open/2/terraform/attempt_2/main.tf` e o `terraform.tfstate` ao lado.
- **task-130, CLI rep 2, harness.** As 3 tentativas pararam em 8192 tokens de saída
  (`max_tokens`), sem o bloco `run.sh`, e a execução virou `FORMAT_FAIL`. Saídas em
  `task-130/2/cli/attempt_*` (ver A4 em `registro-metodologico.md`).

### Sonnet (`runs/20261020T184924-modality-spike`, N = 3)

| Origem | Terraform | CLI | boto3 |
|---|---|---|---|
| enunciado | 0 | 0 | 3 |
| **Total de falhas (de 75)** | **0** | **0** | **3** |

- **task-230, boto3 reps 1–3, enunciado.** O script lista as tabelas, não acha nenhuma,
  imprime "No DynamoDB tables found" e sai com sucesso na 1ª tentativa (falha silenciosa).
  Saídas em `task-230/<rep>/boto3/attempt_1`.

### Leitura revisada

Nenhuma modalidade é melhor que outra em nenhum tipo de caso, com os dois modelos. As diferenças
vistas no Floci vêm de duas fontes: as task-115 e task-230 (enunciado que pressupõe um recurso
inexistente: o Terraform cria, CLI e boto3 procuram e falham) e o `query-sg-ssh-open` (Floci).
Tirando essas tarefas, sobram 4 falhas no Haiku (1 do agente, 2 agente/enunciado e 1 do
harness) e nenhuma no Sonnet. Os números com e sem essas tarefas estão em
`registro-metodologico.md` §3 ("Números da reanálise").
