# Tarefas: formato, datasets e como criar uma nova

Uma task é um diretório com o pedido, o estado inicial e o verificador. O catálogo fica em
`src/iac_harness/modalities/tasks/<id>/` e é usado tanto pelo pipeline de modalidades
(`modality-*`) quanto pelo agente real (`agent-run`). O formato abaixo é o que o carregador
(`src/iac_harness/modalities/loader.py`) lê; o que não está aqui não é lido pelo código.

## Arquivos

| Arquivo | Obrigatório | Papel |
|---|---|---|
| `meta.toml` | sim | metadados (abaixo) |
| `prompt.md` | sim | o pedido, exatamente como o agente vê (o harness acrescenta só a linha de ambiente) |
| `check.py` | sim | verificador do estado final, sem LLM |
| `reference.tf`, `reference.sh`, `reference.py` | uma por modalidade (`terraform`, `cli`, `boto3`), salvo as declaradas em `missing_references` | solução de referência, usada pela pré-validação e pelo replay |
| `initial.tf` | obrigatório em `modify`, `delete` e `query`; proibido em `create` | estado inicial, aplicado com `terraform apply` antes do agente |
| `setup.py` | não | completa o estado que o `initial.tf` não expressa (ex.: versões de objeto e delete markers); roda depois do apply inicial, no mesmo ambiente do check |
| `collateral.py` | não | retrato do que **não** deve mudar; o harness compara antes × depois (`collateral_ok`) |
| `user.md` | não | cartão do usuário simulado: o que a pessoa sabe e responde (só com `[user_sim]`) |
| `check_final.py` | não | verificador do estado **depois** da resposta do usuário simulado (`check_pass_final`) |
| `upstream/` | não | cópias literais de arquivos do dataset de origem (ex.: o `check.py` original do aws-bench), postas ao lado do `check.py` quando ele roda |

`user.md`, `check_final.py`, `setup.py` e `collateral.py` nunca vão para o `/work` do
agente.

### `meta.toml`

Chaves lidas pelo carregador:

| Chave | Valores | Padrão |
|---|---|---|
| `id` | igual ao nome da pasta (senão, erro) | — |
| `category` | `create`, `modify`, `delete`, `query` | — |
| `origin` | de onde veio (ex.: `iac-eval-v2:aws/task-230`, `authored`, `aws-bench:...`, `real-world`) | — |
| `region` | região AWS passada ao check e ao agente | — |
| `expect` | `act` (o certo é agir) ou `ask` (pedido ambíguo: o certo é perguntar) | `act` |
| `hide_initial` | `true`: o agente não recebe o `initial.tf` e descobre o estado sozinho | `false` |
| `answer` | `true`: fora da consulta, também pede resposta em `/work/answer.json` | `false` |
| `missing_references` | lista de modalidades sem referência | `[]` |
| `missing_reason` | por que faltam (obrigatório com `missing_references`) | `""` |

O comentário de cabeçalho do `meta.toml` (linhas `#` no topo) documenta a origem, as adaptações e
o que o check aceita. Ele não é decorativo: o juiz de qualidade das perguntas
(`agent-judge-questions`) o lê como nota dos autores. Outras chaves aparecem em tasks específicas e
são conferidas só pelos testes: `paraphrase_of` (`lay-*`, `tests/test_modalities.py`) e
`upstream_source`, `upstream_commit`, `upstream_sha256`, `[placeholders]`, `[check_env]`
(`awsb-*`, `tests/test_awsbench_tasks.py`).

### Contrato dos scripts

`check.py`, `check_final.py`, `collateral.py` e `setup.py` rodam como `python3 <arquivo> <region>`
dentro da toolchain (com boto3 e as credenciais do backend), num diretório temporário **fora** do
`/work`. Ao lado deles ficam `upstream/` e, se o agente os deixou no `/work`, `answer.json` e
`terraform.tfstate`.

- `check.py` / `check_final.py`: a última linha do stdout é `{"pass": bool, "details": {...}}`.
  Saída que não é esse JSON, ou código de saída diferente de 0, reprova. O script nunca deve
  levantar exceção (erro vira `pass = false`; ver `mod-sqs-retention/check.py`).
- `collateral.py`: a última linha é `{"snapshot": {...}}`; falha do retrato vira
  `collateral_ok = None`, não efeito colateral.
- `setup.py`: código de saída diferente de 0 vira falha do estado inicial (`INITIAL_FAIL`).
- Em `expect = "ask"`, o `check.py` confere o estado **intocado** (o gate é invertido) e o
  `check_final.py`, se houver, confere o estado desejado depois da resposta.
- Na AWS real, um check reprovado depois da solução é refeito por um tempo (consistência
  eventual); `check_attempts` vai nos detalhes.

## Datasets e prefixos

| Prefixo | Qtd. | Origem | Para quê |
|---|---|---|---|
| `task-` | 19 | IaC-Eval v2 (`origin = "iac-eval-v2:..."`), enunciado sem o bloco "Intent" | criação e algumas modificações |
| `awsb-` | 5 | aws-bench, adaptadas (atribuição em `src/iac_harness/modalities/tasks/AWS-BENCH-NOTICE.md`) | tarefas de terceiros com check original em `upstream/` |
| `mod-`, `del-`, `query-` | 8, 3, 2 | autorais | modificação, remoção e consulta, que o IaC-Eval não tem |
| `amb-`, `clr-` | 6, 4 | autorais (E2) | pares ambígua (`expect = "ask"`) × clara, com o mesmo `initial.tf` |
| `lay-` | 10 | autorais (E10) | paráfrase leiga de outra task (`paraphrase_of`); só o `prompt.md` muda |
| `real-` | 9 | casos reais (E13, re:Post, GitHub, HashiCorp Discuss) | pedidos reais com `user.md` e, em parte, `check_final.py` e `setup.py` ([casos-reais.md](casos-reais.md)) |

Fora do catálogo:

- **Ambig-IaC** (E16): não tem diretório por task. Com `[modality] dataset = "ambig-iac"`, as tasks
  vêm de `data/ambig_iac_<revisão>.jsonl` (baixado por `scripts/fetch_ambig_iac.py`) e o
  verificador é o oráculo plan + OPA do dataset, no lugar do `check.py`.
- **Catálogo alternativo:** `[modality] tasks_dir` aponta outro diretório com o mesmo formato. É o
  caso da task de teste `tests/fixtures/user-sim-tasks/sim-fixture-bucket`, usada por
  `configs/e13-usersim-floci.toml`.

Fontes, licenças e critérios de cada dataset: [datasets.md](datasets.md). Estratos (intenção,
alvo, serviços…): `configs/strata.toml`.

## Criar uma task nova

1. **Diretório.** Crie `src/iac_harness/modalities/tasks/<id>/` (ou num catálogo à parte, para não
   mexer no dataset medido; veja o passo 5) com `meta.toml`, `prompt.md` e `check.py`. Copie uma
   task da mesma categoria como modelo: `mod-sqs-retention` (modificação com colateral),
   `amb-del-s3` (ambígua), `real-s3-delete-versions` (usuário simulado, `setup.py`,
   `check_final.py`), `awsb-move-s3-contents-within-bucket` (check de terceiros em `upstream/`).
2. **Estado inicial.** Em `modify`/`delete`/`query`, escreva o `initial.tf`; use `setup.py` só para o
   que o Terraform não expressa. Nomes globais (buckets) precisam de sufixo fixo.
3. **Referências.** Uma por modalidade, ou declare a ausência em `missing_references` com
   `missing_reason`.
4. **Check.** Siga o contrato acima. Em `expect = "ask"`, o check confere o estado intocado e a
   referência é um chute plausível que deve reprovar.
5. **Registro no catálogo.** Uma task em `src/iac_harness/modalities/tasks/` precisa de uma entrada
   em `configs/strata.toml` (`tests/test_agentic_strata.py`) e do id em `EXPECTED_TASKS`
   (`tests/test_modalities.py`); os prompts fora de `real-world` não podem citar "terraform". Para
   experimentar sem tocar no catálogo medido, use um diretório próprio com `[modality] tasks_dir`.
6. **Pré-validação** (abaixo) antes de qualquer run com agente.

## Pré-validar

| Tipo de task | Comando | O que confere |
|---|---|---|
| Catálogo (ou `tasks_dir`), qualquer categoria | `uv run iac-harness modality-prevalidate --config <config> --tasks <id>` | por modalidade: o estado inicial aplica; o check reprova no estado inicial; a referência executa e o check passa. Em `expect = "ask"`, invertido: o check passa no inicial, a referência executa e reprova e, se houver `check_final.py`, a referência passa nele |
| Ambig-IaC | `uv run iac-harness ambig-prevalidate --config <config> --order <selection.json>` (ou `--tasks`, `--target-pass N`) | a referência passa no oráculo e um `main.tf` vazio reprova |
| IaC-Eval v2 no pipeline legado | `uv run iac-harness prevalidate --config <config>` | a referência passa pela cadeia validate/plan/OPA/sandbox |
| Multi-IaC-Eval (legado) | `uv run iac-harness prevalidate-miac --config <config> --csv <test.csv>` | `initial` e `expected` das triplas pela cadeia |

O `modality-prevalidate` grava `runs/<id>-modality-prevalidate/prevalidation.jsonl` (uma linha por
task × modalidade, com `valid`) e `prevalidation_config.json` com a procedência, e imprime uma
tabela. `valid = None` quer dizer referência ausente (nem válida nem inválida). Para pré-validar
só no Floci, use uma config com `[sandbox]` do Floci (ex.: `configs/modality-haiku.toml`); na AWS
real, leia antes [componentes.md](componentes.md#sweep-da-aws-real).

Depois do gate, um trial curto com o agente: copie `configs/quickstart-floci.toml`, troque
`[modality] tasks` e rode `agent-run`.
