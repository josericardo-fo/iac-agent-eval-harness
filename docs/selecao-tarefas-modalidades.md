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
