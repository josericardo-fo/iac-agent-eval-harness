# Configs

Índice das configs: a que estudo ou experimento cada uma pertence, que run ela gerou e se ainda
está em uso. As configs antigas ficam aqui porque os documentos citam o caminho delas. Os runs
estão em `runs/` (fora do git). Os números de experimento (E1–E4, E10–E12) seguem o
`docs/registro-metodologico.md` (ver "Experimentos" no topo dele).

**Status:** **ativa** = ainda vale para runs novos ou para métricas; **histórica** = de uma fase
encerrada, só para reproduzir; **base** = nunca rodou sozinha, as outras derivam dela.

## Fase 1: feedback no loop do agente, condições A/B/C (Semanas 4–6, `docs/protocolo.md`)

| Config | Para quê | Runs | Status |
|---|---|---|---|
| `smoke.toml` | Valida a cadeia, N=1 | `20260930T*-smoke` | ativa (testes de integração) |
| `pilot.toml` | Piloto estratificado, N=1 | `20261014T*-pilot` | histórica |
| `screen.toml` | Triagem, condição A nas 93 tarefas | `20261014T*-screen` | histórica |
| `ablation-rulenames.toml` | Ablação do feedback de intenção (só nomes das regras) | `20261014T225134-ablation-rulenames` | histórica |
| `ablation-full.toml` | Ablação do feedback de intenção (policy completa, teto) | `20261014T235418`, `20261015T010140` | histórica |
| `main-n3.toml` | Rodada principal, 93 tarefas, N=3 | `20261015T010828-main-n3` | histórica |
| `miac.toml` | Pré-validação do Multi-IaC-Eval (segundo Floci) | `20261014T220037`, `20261015T13*-miac-prevalidate` | histórica |
| `mvp.toml` | Execução completa planejada (186 tarefas, N=5) | nenhum | histórica (não rodou) |

As pré-validações de 30/09 e 01/10 (`*-prevalidate`) são desta fase.

## Fase 2: estudo de modalidades, Terraform × AWS CLI × boto3 (Semanas 6–8)

Desenho em `docs/desenho-modalidades.md`. Tarefas e gates em `docs/selecao-tarefas-modalidades.md`.

| Config | Para quê | Runs | Status |
|---|---|---|---|
| `modality-spike.toml` | Sonnet, Floci, 25 tarefas, N=3 | `20261020T184924-modality-spike` | histórica (base das seguintes) |
| `modality-spike-localstack.toml` | Igual, no LocalStack (segunda opinião só para as referências) | nenhum próprio | histórica |
| `modality-haiku.toml` | Haiku, Floci, 25 tarefas, N=3 | `20261027T150905-modality-haiku` | histórica (base do E2 e do E4) |
| `modality-real-aws.toml` | AWS real, 25 tarefas menos a task-201; gate das referências | `20261028T*-modality-prevalidate` | base |
| `modality-real-aws-msk.toml` | AWS real, só task-201 (MSK, cara) | nenhum | histórica (não rodou) |
| `modality-real-aws-agent.toml` | Haiku na real, 18 tarefas comuns ao Floci, N=3 | `20261028T180350-modality-real-aws-agent` | histórica |
| `modality-real-aws-only.toml` | 12 tarefas só da real, N=1 | `20261028T133701-modality-real-aws-only` | histórica |
| `modality-real-aws-only-n3.toml` | R1: as mesmas 12 com N=3 | `20261029T003159-modality-real-aws-only-n3` | histórica |

## Fase 3: experimentos do toolkit (Semanas 8–9, registro §3)

| Exp. | Config | Para quê | Runs | Status |
|---|---|---|---|---|
| E1 | nenhuma | Replay sem LLM (`modality-replay`) | `20261029T19*-replay-*` | histórica |
| E2 | `e2-ask-haiku.toml` | O agente pergunta quando deve? | `20261030T131533-e2-ask-haiku` e o reask `20261030T144537` | histórica |
| E4 | `e4-free-haiku.toml` | Forma livre: escolhe a mais barata? | `20261030T153918-e4-free-haiku` | histórica |
| E3 | `e3-pilot.toml` | Piloto do agente real no Floci | `20261031T003322`, `20261106T114200-e3-pilot` | histórica |
| E3 | `e3-agent.toml` | Matriz Floci completa (35 × 5 × 2 × N=3) | nenhum (rodou em fatias) | base |
| E3 | `e3-agent-haiku.toml`, `e3-agent-sonnet.toml` | Fatias da matriz Floci | `20261106T143311`, `20261106T170449` | histórica |
| E3 | `e3-fix118-haiku.toml`, `e3-fix118-sonnet.toml` | Conferir a correção da skill (task-118) | `20261106T192631`, `20261106T192914` | histórica |
| E3 | `e3-skillfix.toml` | Floci com o toolkit corrigido (decisão E8) | `20261110T144440-e3-skillfix` (+ `-discarded`) | histórica |
| E3 | `e3-pilot-aws.toml` | Piloto na AWS real, 4 tarefas | `20261109T1*`, `20261111T122554-e3-pilot-aws` | ativa (smoke da real) |
| E3 | `e3-agent-aws.toml` | Matriz real (29 × 5 × 2 × N=1) | `20261109T163420-e3-agent-aws` | histórica (base do E12) |
| E11 | `modality-real-aws-amb.toml` | Gate das ambíguas na real | `20261111T125751-modality-prevalidate` | histórica |
| E11 | `e11-amb-aws.toml` | Ambíguas na real, v1 × v2 | `20261111T132723-e11-amb-aws` | histórica |
| E12 | `e12-v2-aws.toml` | Toolkit v2 nas 29 claras da real | `20261111T162415-e12-v2-aws` | histórica |
| todos | `strata.toml` | Estratos das tarefas, lidos pelo `agent-metrics` | n/a | ativa |

O E10 (pedido leigo) ainda não tem config.
