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

### Tarefas autorais (modalidades)
- **Fonte:** este repositório (`origin = "authored"`, ou `"harness"` nas 3 primeiras).
- **Tarefas:** 13: 8 de modificação, 3 de remoção e 2 de consulta
  (`src/iac_harness/modalities/tasks/`).
- **Por quê:** o IaC-Eval só tem criação. Modificação, remoção e consulta são os casos em que o
  paper de referência (Yang et al., 2025) prevê diferença entre modalidades.
- **Validação:** o estado inicial reprova e a referência passa nas 3 modalidades, no Floci e no
  LocalStack (`modality-prevalidate`).

## Avaliados, sem uso por enquanto

### Multi-IaC-Eval (Amazon Science)
- **Fonte:** https://huggingface.co/datasets/AmazonScience/Multi-IaC-Eval, revisão
  `e3dd91b42c13f20a86fb23a2cf8a5d84126f6fc3`, CC-BY-NC-4.0 (adaptador
  `datasets/multi_iac_eval.py`).
- **Tarefas:** 223 triplas (template inicial, pedido, template esperado); 28 executam no Floci
  (run parcial `runs/20261015T135837-miac-prevalidate`).
- **Por que não está em uso:** não tem verificação automática da resposta (a nota original é por
  similaridade e juiz LLM).

## Próximos

### aws-bench (AWS, 2026)
- **Fonte:** https://github.com/aws-bench/aws-bench-datasets, Apache-2.0.
- **Tarefas:** 134 (99 de consulta, julgadas por LLM; 35 de mudança, com `check.py` em boto3
  sobre o estado da conta).
- **Plano:** usar as 35 de mudança no Floci/LocalStack, sem os arquivos de "steering" que mandam
  usar CLI/SDK (ficam fora das instâncias). Contar quantas executam antes de decidir.

### Enhanced IaC-Eval (Nekrasov)
- **Fonte:** https://zenodo.org/records/20272829, CC-BY-4.0.
- **Tarefas:** 458, com referências Terraform corrigidas e validadas no LocalStack. A nota é
  Rego.
- **Uso possível:** fonte de estado esperado para escalar a criação com checagem automática. Os
  enunciados citam Terraform, então não são neutros entre modalidades.
