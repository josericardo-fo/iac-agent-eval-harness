# Scripts de experimento

Scripts de seleção e de uso único, guardados para reproduzir o que o registro metodológico
(`docs/registro-metodologico.md`) cita. Saíram de `scripts/` depois da tag `v1.0.0`; os comandos
do registro valem para a tag. Os sorteios são determinísticos: rodar de novo gera o mesmo arquivo.

| Script | Experimento | O que faz |
|---|---|---|
| `select_modality_tasks.py` | modalidades (Terraform/CLI/boto3) | sorteia as 7 tarefas de criação novas → `src/iac_harness/modalities/selection.json` (critério em `docs/selecao-tarefas-modalidades.md`) |
| `select_real_aws_tasks.py` | modalidades, F2.3 (tarefas só na AWS real) | sorteia as 7 tarefas e as substituições do gate → `src/iac_harness/modalities/selection_real_aws.json` |
| `select_e10_tasks.py` | E10 (pedido leigo) | sorteia as 10 tarefas claras do E3 no Floci → `configs/e10-selection.json` |
| `e15_overnight.sh` | E15 (qualidade das perguntas) | roda os dois juízes das perguntas nos runs do E14 e gera `runs/e15-report.md` |
| `select_e16_tasks.py` | E16 (Ambig-IaC) | sorteia as 50 tarefas entre as que passam no gate → `configs/e16-selection.json` |

`scripts/fetch_ambig_iac.py` fica em `scripts/`: é o passo de instalação do dataset Ambig-IaC
(`docs/tarefas.md`), não um script de uso único.
