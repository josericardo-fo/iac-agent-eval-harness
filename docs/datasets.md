# Datasets do harness

Índice único dos conjuntos de tarefas usados ou avaliados. Cada dataset novo entra com a mesma
ficha. Instâncias de dataset **não são alteradas**. Adaptações (ex.: corte do bloco `## Intent`,
ajuste de referência) ficam registradas no `meta.toml` da tarefa e aqui.

Métricas: o A/B/C usa `iac_harness.metrics`; as modalidades usam `iac_harness.modalities.metrics`
com as mesmas funções e nomes (Wilson, McNemar exato + Holm, bootstrap pareado, custo).

## Em uso

### IaC-Eval v2
- **Fonte:** https://huggingface.co/datasets/iac-eval-v2/iac-eval-v2, revisão
  `defefc108d437f5659f40be66fc863833e9c96fb` (adaptador `datasets/iac_eval_v2.py`).
- **Tarefas:** 186 (AWS, só Terraform). A nota original é OPA/Rego sobre o `terraform plan`.
- **Executáveis no Floci:**
  - **Semana 5:** a pré-validação registrou 93.
  - **`runs/prevalidation-merged`:** marca 76 como `sandbox_supported`; foi o pool do sorteio das
    modalidades.
  - **Diferença 93 × 76:** a conferir.
  - **Referência inválida no harness:** 3.
- **Experimentos:**
  - **A/B/C** (Semana 6): 93 tarefas × 3 condições × 3 repetições
    (`runs/20261015T010828-main-n3`).
  - **Modalidades:** 12 tarefas de criação (5 escolhidas + 7 sorteadas,
    `docs/selecao-tarefas-modalidades.md`), com nota pelo estado final.
- **Achados sobre o dataset:**
  - 13 tarefas falham no OPA por regra fora do enunciado (Conferência, Semana 7);
  - task-230 não diz qual tabela;
  - task-108 usa `expected_bucket_owner` de outra conta;
  - task-233 pede o que a AWS já faz por padrão.
  - task-108, 110, 115 e 118 não passam na AWS real sem alterar o dataset: usam nomes de bucket
    S3 que já pertencem a outras contas (`sample`, `your-bucket-name`,
    `wellcomecollection-platform-infra`, `pike-680235478471`; o `head-bucket` responde 403).
    Devem falhar com `BucketAlreadyExists`/`AccessDenied`. O benchmark supõe um namespace global
    vazio, que só existe no emulador. Não renomeamos (regra de não alterar o dataset).
  - task-183 e task-388 não rodam em conta nova: o Amazon Augmented AI (HumanTaskUI) não aceita
    clientes novos, e a API de vaults do Glacier foi fechada para contas novas. Substituídas no
    sorteio da AWS real (`docs/selecao-tarefas-modalidades.md`).
  - task-367 usa bundle (`micro_1_0`) e blueprint (`mysql_8_0`) do Lightsail descontinuados. A
    referência foi adaptada para `micro_2_0`/`mysql_8_4`; o enunciado não cita versão.

### Tarefas autorais (modalidades)
- **Fonte:** este repositório (`origin = "authored"`, ou `"harness"` nas 3 primeiras).
- **Tarefas:** 13: 8 de modificação, 3 de remoção e 2 de consulta
  (`src/iac_harness/modalities/tasks/`).
- **Por quê:** o IaC-Eval só tem criação. Modificação, remoção e consulta são os casos em que o
  paper de referência (Yang et al., 2025) prevê diferença entre modalidades.
- **Validação:** o estado inicial reprova e a referência passa nas 3 modalidades, no Floci e no
  LocalStack (`modality-prevalidate`).

### aws-bench (AWS, 2026)
- **Fonte:** https://github.com/aws-bench/aws-bench-datasets, commit
  `73f814af0f3ba381e1a797801333ad4478b78971`, Apache-2.0. Atribuição e lista de arquivos
  copiados em `src/iac_harness/modalities/tasks/AWS-BENCH-NOTICE.md`.
- **Tarefas no dataset:** 134 (99 de consulta, julgadas por LLM; 35 de mudança, com `check.py`
  em boto3 sobre o estado da conta).
- **Em uso:** 5 tarefas de `compute-and-data`, só na AWS real (`tasks/awsb-<nome>/`):
  - `create-s3-bucket-with-config` (criação, com resposta em JSON);
  - `move-s3-contents-within-bucket`;
  - `sync-s3-buckets-with-metadata`;
  - `dynamodb-strip-ec2-fields-from-prod`;
  - `lambda-versioned-alias-update`.

  A sexta prevista, `invalidate-cloudfront-cache`, ainda não entrou. O enunciado cita o domínio
  `dXXXX.cloudfront.net`, e o check precisa do `DistributionId`. A AWS só atribui os dois no
  apply do estado inicial, e o `prompt.md` é fixo. Falta decidir como adaptar.
- **Adaptações** (registradas também no cabeçalho do `meta.toml` de cada tarefa):
  - **Arquivos originais:** `upstream/instruction.md` e `upstream/check.py` são cópias literais
    do commit fixado. O sha256 de cada um fica no `meta.toml`, e
    `tests/test_awsbench_tasks.py` confere.
  - **Enunciado:** `prompt.md` é gerado do `instruction.md` por uma transformação
    determinística (o teste confere):
    - os `{{...}}` viram os nossos nomes (tabela `[placeholders]`);
    - sai o parágrafo "IMPORTANT: Write your final prose answer to
      `/logs/agent/agent-output.txt`.";
    - "Additionally, write `/logs/agent/agent-output.json` containing exactly:" vira "Answer
      with this JSON object:", com o mesmo bloco JSON (`answer = true`, como nas consultas).
  - **Estado inicial:** um `initial.tf` reduzido no lugar do cenário CDK inteiro. Tem só os
    recursos da tarefa e os dados do `pre_invoke/reset.py`:
    - objetos S3 com o conteúdo literal;
    - os 100 itens do DynamoDB, gerados uma vez pelo gerador determinístico e embutidos;
    - a Lambda com o código inline do CDK.

    O agente não vê o `initial.tf` (`hide_initial`), como no original, em que só recebe o
    enunciado.
  - **Nomes:** fixos, com prefixo `awsb-`. Os buckets têm sufixo aleatório fixo, porque os nomes
    são globais.
  - **Check:** o `upstream/check.py` roda sem modificação, por um `check.py` adaptador. O
    adaptador:
    - define as variáveis do `[verifier.env]` com os nossos nomes (`[check_env]`);
    - injeta um `rewardkit` falso que registra os `@criterion`;
    - troca no texto só o caminho `/logs/agent/agent-output.json` por `answer.json`.

    `pass` só se todos os critérios passarem, e cada critério sai em `details`.
  - **Sem referência Terraform** (`missing_references = ["terraform"]`) em `move-s3`, `sync-s3` e
    `dynamodb-strip`. São operações do plano de dados sobre objetos e itens que já existem
    (mover, copiar trocando metadados, remover atributos), e o provider aws não tem recurso
    nativo para elas, só `local-exec`. Os prompts não mudam; nessas tarefas o gate vale para CLI e
    boto3, e o uso de `local-exec` pelo agente é medido à parte. `create-s3` tem referência
    Terraform. A da Lambda importa a função existente com um bloco `import` e muda só o valor da
    variável de ambiente.



### Multi-IaC-Eval (Amazon Science)
- **Fonte:** https://huggingface.co/datasets/AmazonScience/Multi-IaC-Eval, revisão
  `e3dd91b42c13f20a86fb23a2cf8a5d84126f6fc3`, CC-BY-NC-4.0 (adaptador
  `datasets/multi_iac_eval.py`).
- **Tarefas:** 223 triplas (template inicial, pedido, template esperado); 28 executam no Floci
  (run parcial `runs/20261015T135837-miac-prevalidate`).
- **Por que não está em uso:** não tem verificação automática da resposta (a nota original é por
  similaridade e juiz LLM).

## Próximos

### Enhanced IaC-Eval (Nekrasov)
- **Fonte:** https://zenodo.org/records/20272829, CC-BY-4.0.
- **Tarefas:** 458, com referências Terraform corrigidas e validadas no LocalStack. A nota é
  Rego.
- **Uso possível:** fonte de estado esperado para escalar a criação com checagem automática. Os
  enunciados citam Terraform, então não são neutros entre modalidades.
