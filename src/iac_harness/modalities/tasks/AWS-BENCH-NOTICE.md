# Atribuição: aws-bench-datasets

As pastas `awsb-*/upstream/` contêm cópias literais, sem modificação, de arquivos do projeto
**aws-bench-datasets** (https://github.com/aws-bench/aws-bench-datasets), commit
`73f814af0f3ba381e1a797801333ad4478b78971`, distribuído sob a Apache License 2.0
(https://www.apache.org/licenses/LICENSE-2.0).

NOTICE do projeto original:

```
aws-bench-datasets
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
```

Arquivos copiados (origem `tasks/compute-and-data/<tarefa>/`):

| Pasta aqui | `upstream/instruction.md` ← | `upstream/check.py` ← |
| --- | --- | --- |
| `awsb-create-s3-bucket-with-config` | `instruction.md` | `tests/check.py` |
| `awsb-move-s3-contents-within-bucket` | `instruction.md` | `tests/check.py` |
| `awsb-sync-s3-buckets-with-metadata` | `instruction.md` | `tests/check.py` |
| `awsb-dynamodb-strip-ec2-fields-from-prod` | `instruction.md` | `tests/check.py` |
| `awsb-lambda-versioned-alias-update` | `instruction.md` | `tests/check.py` |

O sha256 de cada cópia está no `meta.toml` da tarefa (`upstream_sha256`), e
`tests/test_awsbench_tasks.py` confere os hashes.

Obras derivadas, modificadas por este repositório: `prompt.md` (gerado do `instruction.md`), o
`check.py` adaptador, `initial.tf` (derivado das stacks CDK e dos `pre_invoke/reset.py`) e as
referências (derivadas de `solution/solve.sh`). Cada adaptação está registrada no `meta.toml` da
tarefa e em `docs/datasets.md`.
