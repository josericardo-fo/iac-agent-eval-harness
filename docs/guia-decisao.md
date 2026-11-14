# Guia de decisão: Terraform, AWS CLI ou boto3, e quando usar o toolkit

Rascunho de 14/11/2026. Não traz número novo: cada linha remete ao `registro-metodologico.md` (§3)
ou ao `regras-achados.md`. Só entra aqui o que tem medida; o que não tem fica na seção "Sem base".

**Força da evidência.**
- **Forte:** repetido em mais de um run (Floci e AWS real, ou dois modelos), N=3, com teste
  (bootstrap pareado por tarefa; Holm).
- **Descritiva:** direção consistente em mais de uma rodada, sem teste estatístico ou com poucas
  tarefas.
- **Fraca:** uma rodada, poucas tarefas ou um modelo só.

**Vale para:** Claude Code e o protocolo do harness, com Haiku 5.5 e Sonnet 5.5, AWS em us-east-1
(e o emulador Floci), tarefas do IaC-Eval e de autoria nossa, um prompt por forma e o bloco do
gateway corporativo. Fora disso, é hipótese.

## 1. Qual forma usar

| # | Recomendação | Evidência | Força |
|---|---|---|---|
| 1 | **Não escolha a forma pelo acerto.** Nas tarefas claras, Terraform, CLI e boto3 acertam igual em criar, modificar, remover e consultar. | Haiku e Sonnet no Floci (N=3), 18 tarefas comuns na real (51, 51 e 49 de 54) e 12 só na real (N=3): Holm = 1 em todos os pares, todos os ICs do bootstrap com o zero. As diferenças aparentes vinham de 2 tarefas de premissa falsa e de uma limitação do emulador (registro §3, A1, A2, "Números da reanálise", "Emulador × AWS real", "R1"). | Forte (resultado nulo) |
| 2 | **Para criar e modificar, prefira Terraform: gasta menos tokens.** | Bootstrap pareado por tarefa: IC sem o zero nos 3 runs com tarefas comuns e no R1 contra o CLI. No total, o CLI gasta de 1,2× a 2,0× o Terraform. Continua sem as tarefas de premissa falsa (registro §3, "Análises de 29/10", H1; a hipótese original previa o contrário). | Forte |
| 3 | Para consultar, CLI ou boto3 só leitura gastam menos. | Só 2 tarefas de consulta (registro §3, "Análises de 29/10"). | Fraca |
| 4 | **Não é preciso mandar o agente escolher a forma.** Sem dica, ele já escolhe Terraform para criar e modificar, e CLI para consultar, e fica perto da forma mais barata. | E4: 1ª escolha de acordo com a recomendação em 61 de 66 trajetórias; tokens de saída 1,19× os da forma mais barata e 0,44× os da mais cara; acerto 72/75 (registro §3, "E4"). Só Haiku, Floci. | Descritiva |
| 5 | **Antes de agir sobre algo que o pedido supõe existir, confira se existe.** Com Terraform, um "sucesso" pode esconder a premissa falsa: ele cria o recurso que faltava. CLI e boto3 procuram, não acham e falham. | task-115 e task-230, no Floci e na real (task-230: Terraform 3/3, CLI e boto3 0/3) (registro §3, A2). | Descritiva (2 tarefas) |
| 6 | **Depois de aplicar, releia o estado, sobretudo com Terraform.** Nas tarefas mais fáceis, as falhas do Terraform são silenciosas (execução ok, estado errado) e as do CLI e do boto3 dão erro. Nas mais difíceis, as três formas falham em silêncio. | Falha silenciosa entre as falhas: Haiku Floci TF 3/3, CLI 0/7, boto3 0/8; real (18 comuns) 3/3, 0/3, 0/5; R1 (só real, mais difíceis) 6/11, 8/12, 6/9 (registro §3, "Análises de 29/10"). | Descritiva |

## 2. Quando usar o toolkit (skill + regras)

| # | Recomendação | Evidência | Força |
|---|---|---|---|
| 7 | **Use o toolkit pelo que ele muda nos pedidos ambíguos:** ele troca chute por pergunta. | Floci: perguntou em 18/18 ambíguas com a skill × 10–12/18 sem (Haiku e Sonnet). Real (E11, N=1): `none` 8/12 × v1 12/12 × v2 11/12 (registro §3, "E3: matriz no Floci", "E11/E12"). O `asked` não tem teste estatístico; o veredito é de um juiz LLM (Sonnet), e um segundo juiz de outro fornecedor (`gpt-6-luna`, E14) concorda em 1790 de 1806 trajetórias, sem mudar nenhuma conclusão. | Descritiva, consistente em 3 rodadas |
| 8 | **Não espere ganho nos pedidos claros**, e conte com mais tokens. | E3 real (29 tarefas, N=1): nenhuma condição difere de `none` (Holm ≥ 0,5). E12: a v2 só ganha nas 3 tarefas que a originaram; fora delas, empata com a v1. E10: 1,2× a 2,4× os tokens de `none` por tarefa, sem ganho (registro §3, "E3: matriz na AWS real", "E11/E12", "E10"). | Forte para "não ganha"; descritiva para o custo |
| 9 | Pedido em linguagem leiga custa pouco acerto, e o toolkit não muda isso. As falhas foram paradas para perguntar, não estado errado. | E10 (Haiku, Floci, N=3): técnico × leigo, `none` 30/30 × 27/30 e `own-rules-v2` 29/30 × 27/30; em 5 das 7 falhas o agente parou por notar o emulador. Mesma leitura com o segundo juiz (registro §3, "E10"). | Fraca |
| 10 | Efeito colateral (mexer no que não devia) não foi observado. | E1: 0 em 180 replays (limite superior de Wilson ~2%). E3 real: 3 marcados em `mod-sqs-retention`, todos com o "antes" sem a fila recém-criada (provável atraso do `ListQueues`, artefato da medida; registro §3, "Colateral na AWS real"). | Descritiva ("não observado") |

## 3. Sem base (não recomendar)

- **Remover:** com que forma remover. Só 3 tarefas de remoção; "remova com o que criou" é hipótese
  (`regras-achados.md`, linha 4; agenda E5 não rodou).
- **Ferramentas além do texto** (`whoami`, `inventory`, `verify`, `diff_baseline`): a condição
  `own-tools` não ganhou de `own-rules` no Floci nem na real.
- **O retorno do erro ajuda mais as formas imperativas** (H3): não se sustentou.
- **Dificuldade da tarefa** (rótulo do IaC-Eval): não prevê o acerto; o que pesa é o enunciado vago.

## 4. Limites que valem para o guia inteiro

- Tarefas de autoria nossa ao lado das do IaC-Eval (T5); um prompt por forma (D3).
- Real com N=1 na maior parte do E3; N=3 só nas modalidades.
- Dois modelos da mesma família, um agente (Claude Code).
- Juiz LLM nas métricas de pergunta (linhas 7 a 9): dois juízes de fornecedores diferentes
  concordam, mas sem leitura humana completa.
- O bloco de diretrizes do gateway entra em todo pedido: as taxas absolutas não valem para a API
  direta.
