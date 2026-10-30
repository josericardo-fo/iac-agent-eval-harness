# Registro metodológico do teste de modalidades

Criado em 28/10/2026. Liga cada decisão de método a uma técnica consolidada, diz onde seguimos,
onde adaptamos e onde a decisão é nossa, e marca o que fazer com ela. As referências foram
conferidas na fonte (lista no fim); o que não foi possível conferir está dito.

**Como ler.**

- **Base:** **Segue** = faz como a referência; **Adapta** = parte da referência e muda um ponto
  (motivo dito); **Própria** = sem precedente direto, justificada por achado ou pelos critérios
  do projeto (justo, reproduzível, realista).
- **Ação:** **Manter**; **Reanalisar** (os dados existentes bastam, custo zero); **Re-rodar**
  (precisa de run novo); **Declarar** (só escrever como limitação).

## 1. Decisões

### Desenho

| # | Decisão | Base | Referência | Ação |
|---|---|---|---|---|
| D1 | Comparar Terraform × CLI × boto3 por tipo de caso | Adapta | Yang et al. [1] compararam SDK, CLI, IaC e portal no Azure, com 11 tarefas de VM e um agente diferente para o portal. Aqui: o mesmo modelo nas 3 formas, AWS, 4 tipos de caso. Não é replicação, é a mesma pergunta com controle. | Manter. Escrever "replica a pergunta", não "replica o estudo". |
| D2 | Desenho pareado: as mesmas tarefas e repetições nas 3 formas | Segue | Miller [8] §4.2: diferença pareada por questão reduz a variância "de graça". | Manter |
| D3 | Prompt-base comum + bloco mínimo e simétrico por forma, versionado (`mod-v3`) | Adapta | SWE-agent [6]: a interface muda o resultado (18,0% × 11,0%), então é variável confundida. Terminal-Bench [5] usa um scaffold neutro. Yang et al. [1] variam o prompt (8 tentativas com prompts diferentes); aqui ele é fixo. | Manter. Declarar a sensibilidade ao texto (Sclar et al. [10]). |
| D4 | Fora do escopo: ClickOps e monitoramento | Própria | O emulador não tem console; não há oráculo para monitoramento. | Manter |
| D5 | Mesmo `max_tokens` (8192) nas 3 formas | Própria | Justiça: o mesmo orçamento. | Manter. task-130 classificada como limite do harness (A4); re-rodar só se pesar (R2). |

### Tarefas

| # | Decisão | Base | Referência | Ação |
|---|---|---|---|---|
| T1 | Sorteio com semente e critério escrito antes, sem escolha à mão; substituição pela fila | Própria (boa prática) | Evita escolher tarefas que favorecem uma forma. É o ponto mais forte do método. | Manter |
| T2 | Enunciado byte a byte, sem editar o dataset; tarefas ambíguas ficam como achado | Própria | Regra do projeto. O ABC [2] T.7/T.10 pede verificar a tarefa e inspecionar quem falha sempre. Aqui: verificado e mantido, com relato. | Manter. Relatar com e sem as tarefas de premissa ambígua (A2). |
| T3 | Excluir enunciados que citam "terraform"/"HCL" | Própria | Justiça entre formas: o pedido não pode nomear uma delas. | Manter |
| T4 | Gate: o estado inicial reprova e a referência passa em cada forma | Segue | ABC [2] T.8/T.9 (tarefa resolvível, solver oráculo) e R.13 (agente trivial). O estado inicial reprovando faz o papel do "agente que não faz nada". Terminal-Bench [5] §2.3 (Solvability). | Manter |
| T5 | Tarefas autorais de modificar, remover e consultar | Própria | O IaC-Eval só tem criação; Yang et al. [1] acham a diferença justamente em updates. | Manter. Declarar a autoria (risco de viés de quem escreve). |
| T6 | Adaptar a referência (nunca o enunciado) quando ela não realiza o pedido ou a API saiu | Própria | Registrado no `meta.toml`. O agente nunca vê a referência (ABC [2] T.5). | Manter |
| T7 | E2: 6 pares claro × ambíguo (`expect = "act"` / `"ask"` no `meta.toml`), 10 tarefas novas + `mod-s3-versioning` e `mod-sqs-retention` como claras. Tipos: mais de um candidato (4), premissa falsa (1), valor faltando (1); 4 de modificar e 2 de remover. O par compartilha o `initial.tf`, e o agente vê o ambiente (P3). | Própria; adapta o gate | Nenhuma base de IaC tem resposta certa "perguntar" (lacuna L5). Gate (ABC [2] T.8/T.9) invertido nas ambíguas: o initial **passa** no check (estado intacto) e a referência, um chute plausível, **reprova** nas 3 formas; nas claras, o gate de sempre (T4). | Registrado em 29/10, antes do run. Autoria nossa (T5). Poder baixo: leitura exploratória. |

### Oráculo

| # | Decisão | Base | Referência | Ação |
|---|---|---|---|---|
| O1 | A nota é o estado final via API (`check.py`), não o artefato | Segue | AppWorld [3] §3.2, τ-bench [4] §3 e Terminal-Bench [5] §2.1 ("testam o estado final, não os comandos"). | Manter |
| O2 | O check aceita qualquer solução que deixe o estado certo (ex.: qualquer tabela com PITR na task-230) | Segue | ABC [2] O.g.1 (todas as saídas válidas); AppWorld [3] (conjunto de estados válidos). | Manter |
| O3 | Efeito colateral (mexer no que não devia) | Parcial | ABC [2] O.g.2 e AppWorld [3] (C_expect/C_allow) pedem isso. Hoje só os checks de remoção e alguns de modificação conferem o que deve continuar igual (ex.: `del-sqs-dev` confere as filas prod). | Listados em 28/10 (§3: 3 sim, 3 parcial, 31 não). Medido à parte no E1 (O7), sem mudar os checks. |
| O4 | Resposta de consulta por `output` (Terraform) ou `answer.json` | Segue | τ-bench [4]: r_output. | Manter |
| O5 | Na AWS real, o check reprovado é refeito por até 60 s | Adapta | ABC [2] O.f.2 (tirar o não determinismo). É igual nas 3 formas, só depois da solução, nunca no baseline. | Manter |
| O6 | `success` (execução ok + estado) e `state_pass` (só estado) lado a lado | Própria | `state_pass` é o comparável com [3][4][5]; `success` é o critério mais estrito do protocolo. | Manter |
| O7 | E1: replay sem LLM dos artefatos finais no Floci, com `collateral_ok` por retrato antes/depois | Adapta | AppWorld [3] compara o estado antes e depois e só aceita mudança no que é permitido (C_expect/C_allow); ABC [2] O.g.2. Aqui o "permitido" é só o atributo-alvo: um `collateral.py` por tarefa retrata o que deve ficar igual (ID/ARN, data de criação, contagem de recursos do tipo, atributos fora do alvo), e `collateral_ok` = retratos iguais. Escopo: 8 de modificar + 2 de consultar, runs Sonnet e Haiku do Floci. O `check.py` é reexecutado sem mudança; a concordância com o `check_pass` original é relatada como reprodutibilidade, e discordâncias são declaradas, não corrigidas. Antes do run, cada retrato é validado: as referências dão `True` e um script que recria o recurso dá `False` (teste de integração). Limite declarado: no S3 e no SQS a recriação só aparece pela data de criação, que o Floci dá com resolução de segundo; recriar no mesmo segundo passaria. | Registrado em 29/10, antes do run. Métrica à parte; não muda nenhuma nota. |

### Protocolo do agente

| # | Decisão | Base | Referência | Ação |
|---|---|---|---|---|
| P1 | Script inteiro + correção pelo erro, até 3 tentativas; a nota nunca volta ao agente | Adapta | Reflexion (protocolo [14]) e SWE-agent [6] (feedback informativo e conciso); ABC [2] T.5 (isolar o agente do gabarito). Não é um agente com várias ferramentas: é uma geração por tentativa. | Manter. Declarar que mede "gerar e corrigir", não exploração livre. |
| P2 | Erro truncado em começo + fim | Segue | mini-swe-agent (corte no meio da saída longa). | Manter |
| P3 | Terraform modifica herdando o tfstate; CLI/boto3 recebem o `initial.tf` como descrição | Adapta | Yang et al. [1] dizem que o IaC falhou por não levar o estado ao modelo. Aqui o estado é dado, o que testa o mecanismo. | Manter. Declarar a assimetria inerente à forma. |
| P4 | E2: desfecho "perguntou". Com `allow_question`, o prompt (`mod-v3-ask`) ganha uma frase neutra, sem exemplos, igual nas 3 formas: o agente pode entregar `question.md` no lugar do arquivo. Nesse caso nada executa e a trajetória termina (`ASKED`). Se ele entregar os dois, vale a pergunta (`both_files`). Acerto: na ambígua, perguntar; na clara, agir com sucesso (perguntar a mais conta como erro). O check roda igual e dá `untouched` (estado intacto) quando o agente chuta na ambígua. | Adapta | τ-bench [4] mede a interação com um usuário simulado; aqui não há simulador (seria uma variável nova) e a pergunta encerra. Nekrasov (protocolo §15): as falhas que sobram são de intenção. | Registrado em 29/10, antes do run. A pergunta vale em qualquer tentativa, mas a feita só depois de um erro de execução (cujo texto muda com a forma) é relatada à parte: `asked_first_attempt` ao lado de `asked`; `untouched` só entre as ambíguas que executaram algo. Limites: a pergunta conta pela existência, não pelo conteúdo (o texto é lido à mão e relatado); a frase é sensível ao texto (D3, Sclar [10]); o `mod-v3` dos runs antigos não muda. |

### Ambiente

| # | Decisão | Base | Referência | Ação |
|---|---|---|---|---|
| E1 | Emulador (Floci) como base, com reset por restart e versões fixadas | Segue | ABC [2] T.1, T.4 e T.6 (versões, limpeza, ambiente congelado). | Manter |
| E2 | LocalStack como segunda opinião só para as referências | Própria | Separa o efeito do emulador do efeito da tarefa. | Manter |
| E3 | AWS real só num subconjunto | Adapta | ABC [2] T.6 desaconselha recurso externo dinâmico. Os achados confirmam o porquê (APIs fechadas, nomes de bucket de outra conta). A real entra como validade externa, não como base. | Manter |
| E4 | Trava de custo (IAM) e task-416 como "bloqueio de permissão" | Própria | ABC [2] R.8/R.11 (relatar o impacto de falhas inevitáveis). Separada dos erros do agente, com totais com e sem ela. | Manter |

### Estatística

| # | Decisão | Base | Referência | Ação |
|---|---|---|---|---|
| S1 | McNemar exato + Holm entre os 3 pares | Adapta | Dietterich [9] recomenda McNemar para comparar dois classificadores no mesmo conjunto (conferido só o resumo). Holm [11]. **Problema:** os pares são (tarefa, rep), e com N=3 cada tarefa entra 3 vezes. As repetições não são independentes (Miller [8] §2.2: o erro padrão ingênuo chega a ser ~3× menor). | Corrigido em 28/10: 1 par por tarefa, pela maioria das reps (A3). |
| S2 | Bootstrap pareado reamostrando tarefas | Segue | Miller [8] (agrupar por questão). Já está implementado e é o teste certo para N>1. | Manter, como teste principal. |
| S3 | Intervalo de Wilson | Segue | Wilson [12]; Brown, Cai e DasGupta [13]. ABC [2] R.10. | Manter |
| S4 | N=3 no emulador; N=1 na AWS real | Adapta | Terminal-Bench [5]: ≥ 5 runs com IC 95%. Miller [8] §3.1: repetir reduz variância. N=1 só permite leitura exploratória. | Feito o R1 (real com N=3, 29/10). O N=1 de 28/10 fica declarado como exploratório. |
| S5 | pass@k só com k ≤ N | Segue | Chen et al. (protocolo [13]): n ≥ k. τ-bench [4]: pass^k, que mede consistência (todas as k tentativas acertam). | Manter. pass^3 relatado em 29/10 (§3). |

## 2. Auditoria pelo ABC [2]

| Item | Situação |
|---|---|
| T.1 versões / T.4 limpeza / T.6 ambiente congelado | Atende (digest, lock, `prompt_version`; reset; emulador fixo) |
| T.5 agente isolado do gabarito | Atende (referência e check fora do workspace; nota não volta) |
| T.7 verificar tarefa / T.8 resolvível / T.9 oráculo | Atende (gate de pré-validação) |
| T.10 inspecionar outliers | Atende. Feito também para as tarefas que decidiam as manchetes (A1, A2) e com as falhas por origem. |
| O.g.1 todas as saídas válidas | Atende |
| O.g.2 efeito colateral | Parcial (O3) |
| O.g.3 mudança trivial não passa | Atende (o baseline reprova) |
| R.5 relação entre construto e medida | Falta escrever: mede "gerar e corrigir um artefato numa forma", não "agente de nuvem" em geral |
| R.7–R.9, R.11 falhas conhecidas e impacto | Atende (listas do Floci, achados do dataset, manchetes relidas em A1/A2, falhas por origem) |
| R.10 significância e IC | Atende (S1 corrigido) |
| R.13 agente trivial | Atende (baseline do estado inicial) |
| R.3 contaminação | Declarar (dataset público desde 2024; mais Terraform que boto3 nos dados de treino, cf. MultiPL-E [14]) |

## 3. Achados que mudam a leitura dos resultados

**A1. "Haiku: Terraform pior em consultar (4/6 × 6/6)" é efeito do emulador.**
- As duas falhas são do `query-sg-ssh-open` (reps 2 e 3), e nas duas a resposta lista os 5 grupos.
- Na rep 3, o código usa os filtros `ip-permission.*`, que o Floci ignora (limitação já listada em `desenho-modalidades.md`).
- Na rep 2, a lógica filtra as regras certas, mas o Floci devolve todas. Revisão (28/10): o
  código separa entrada e saída só pelo filtro `is-egress`, que não consta da documentação de
  `DescribeSecurityGroupRules`; a falha fica como agente/emulador até o run da AWS real
  (`selecao-tarefas-modalidades.md`, "Falhas por origem no Floci").
- Conclusão: não sustenta "Terraform pior em consultar". **Confirmado na AWS real (28/10):** `query-sg-ssh-open` em Terraform passa 3/3 (no Floci, 1/3).

**A2. "Terraform melhor em criar (36/36 × 29 × 28)" vem de 2 tarefas de premissa ambígua.**
- 12 das 15 falhas de CLI e boto3 em criação são das task-115 e task-230. Os enunciados pressupõem um recurso que não existe (bucket `pike-…`, "the DynamoDB table").
- O Terraform cria o recurso e passa; CLI e boto3 procuram o recurso, não acham e falham.
- Sem as duas: 30/30, 29/30 e 28/30, um empate.
- Conclusão: o achado real é "diante de enunciado que pressupõe um recurso inexistente, a forma declarativa cria e a imperativa falha". Esse achado vale para o toolkit; "Terraform cria melhor" não se sustenta.

**A3. McNemar com repetições como pares independentes (S1).**
- Os p-values atuais tratam 3 reps da mesma tarefa como 3 observações.
- O efeito é tornar o teste otimista. Hoje nada deu significativo, então a conclusão não muda.
- Corrigido em 28/10: 1 par por tarefa (números abaixo).

**A4. task-130 CLI rep 2: estouro de `max_tokens` nas 3 tentativas.**
- Conta hoje como `FORMAT_FAIL` do agente, mas é limite do harness (o HANDOFF já classifica assim).

### Números da reanálise (28/10/2026)

McNemar por tarefa (maioria das reps) + Holm; diferença do bootstrap pareado por tarefa (segunda − primeira) com IC 95%. Comando: `uv run iac-harness modality-metrics <run> [--exclude-tasks ...]`.

| Run | Criar (TF / CLI / boto3) | Consultar | Total | Leitura |
|---|---|---|---|---|
| Haiku, Floci, N=3 | 36/36, 29/36, 28/36 | 4/6, 6/6, 6/6 | 72, 68, 67 de 75 | Holm = 1 em tudo. Único IC sem o zero: criar TF×boto3, −0,22 [−0,44; −0,03]. |
| ↳ sem task-115/230 | 30/30, 29/30, 28/30 | — | 66, 68, 67 de 69 | Criar: 0 tarefas discordantes. O IC acima vinha só dessas 2 tarefas. |
| ↳ sem query-sg-ssh-open | — | 3/3 nas 3 | — | A desvantagem do TF em consultar vinha só dessa tarefa. |
| Sonnet, Floci, N=3 | 36/36, 36/36, 33/36 | 6/6 nas 3 | 75, 75, 72 de 75 | As 3 falhas do boto3 são task-115/230; sem elas, 100% em tudo. |
| Haiku, AWS real, N=1 | 7/8, 6/8, 5/8 | — | 9, 8, 8 de 12 | Holm = 1; ICs largos (±0,25 a ±0,75). Exploratório. |

**Conclusão:** com o protocolo atual, nenhuma modalidade é melhor que outra em nenhum tipo de caso. As diferenças vistas vêm de duas fontes:
- um padrão de comportamento: enunciado que pressupõe um recurso inexistente (o Terraform cria; CLI e boto3 falham);
- uma limitação do emulador.

Com 2 a 12 tarefas por categoria, o McNemar por tarefa quase não tem poder (com 2 discordantes, o menor p é 0,5). Por isso o bootstrap é o teste a relatar. ICs com um limite exatamente em 0 vêm de pares sem nenhuma tarefa a favor do outro lado.

### Emulador × AWS real, mesmas 18 tarefas (28/10/2026)

Haiku, N=3 nos dois: `runs/20261027T150905-modality-haiku` (filtrado às 18 tarefas) × `runs/20261028T180350-modality-real-aws-agent`. São as tarefas cuja referência passa nas 3 formas no Floci e na real.

| Categoria | Floci (TF / CLI / boto3) | Real (TF / CLI / boto3) |
|---|---|---|
| Criar (5) | 15, 11, 11 de 15 | 15, 12, 10 de 15 |
| Remover (3) | 8, 9, 9 de 9 | 7, 9, 9 de 9 |
| Modificar (8) | 24 de 24 nas 3 | 24 de 24 nas 3 |
| Consultar (2) | 4, 6, 6 de 6 | 5, 6, 6 de 6 |
| **Total real** | — | **51, 51, 49 de 54** |

- **Real:** nenhum par significativo (Holm = 1). Os ICs do bootstrap incluem o zero (ex.: TF×boto3, [−0,20; +0,11]).
- **Leitura:** o emulador reproduz a nuvem real nessas tarefas. Das 54 combinações tarefa × forma, só 5 mudam o número de acertos, e cada uma em no máximo 2 de 3.
- **Diferença explicada pelo emulador:** só `query-sg-ssh-open` TF (1/3 → 3/3), já esperada (A1).
- **As outras 4 são variação do agente entre repetições, não do ambiente:**
  - `del-s3-bucket` TF, 2 → 1: o mesmo erro do Floci, esvaziar o bucket e mantê-lo no `main.tf`;
  - `task-231` boto3, 2 → 1;
  - `task-130` CLI, 2 → 3: no Floci, uma rep estourou `max_tokens`;
  - `query-dynamodb-ondemand` TF, 3 → 2: na rep 2, o agente **recriou as 4 tabelas** como `resource` em vez de lê-las, e a resposta saiu vazia. É um efeito colateral numa tarefa de consulta (ABC O.g.2), pego por acaso pela resposta errada.
- **O padrão de A2 se repete na real:** task-230 falha 3/3 em CLI e boto3 e passa em TF.
- **Consequência para o método:** o emulador é base válida para essas tarefas (E1, E3). A real acrescenta validade externa, não muda a conclusão.

### R1: tarefas só na AWS real com N=3 (29/10/2026)

As mesmas 12 tarefas do run N=1, agora com N=3 (`runs/20261029T003159-modality-real-aws-only-n3`, Haiku).

| | Terraform | CLI | boto3 |
|---|---|---|---|
| Criar (8) | 19/24 | 17/24 | 19/24 |
| Modificar (4) | 6/12 | 7/12 | 8/12 |
| **Total N=3** | **25/36** | **24/36** | **27/36** |
| Total N=1 (28/10) | 9/12 | 8/12 | 8/12 |

- **Nada significativo:** Holm = 1 em todos os pares; todos os ICs do bootstrap incluem o zero.
- **A ordem mudou de N=1 para N=3:** em N=1 o Terraform aparecia na frente; em N=3 o boto3 aparece. Isso mostra na prática por que N=1 é só exploratório (S4).
- **Tarefas instáveis** (acertam em parte das reps): 1/3 das tarefas no Terraform, 1/4 no CLI, 1/6 no boto3.
- **Falhas por origem:**
  - **enunciado:** `awsb-move-s3` 0/9 (o caminho dado está errado de propósito);
  - **permissão:** task-416, 1/9; só passou quando o agente escolheu uma classe micro (CLI, rep 3);
  - **agente:**
    - task-198, 3/9: o grupo do Redis com o usuário `default`;
    - `awsb-dynamodb-strip`, 4/9;
    - task-367, 6/9;
    - `awsb-lambda-alias` TF, 2/3.
- **`local-exec`:** o Terraform o usou em 6 trajetórias, nas tarefas do aws-bench sem recurso nativo.
- **Leitura:** confirma a conclusão geral. Nas tarefas mais difíceis e só na real, nenhuma forma se destaca; o que pesa é a tarefa.

### pass^3: consistência por forma (29/10/2026)

τ-bench [4]: a tarefa conta só se as 3 reps passam (`check_pass`). Entre parênteses, pass@3 (ao menos 1 das 3).

| Run | Terraform | CLI | boto3 |
|---|---|---|---|
| Sonnet, Floci | 25/25 (25) | 25/25 (25) | 24/25 (24) |
| Haiku, Floci | 23/25 (25) | 22/25 (23) | 21/25 (23) |
| Haiku, real, 18 comuns | 16/18 (18) | 17/18 (17) | 16/18 (17) |
| Haiku, real, 12 só na real (R1) | 6/12 (10) | 7/12 (10) | 8/12 (10) |

- **Haiku, Floci:** o que tira cada forma do 100% já está explicado:
  - Terraform: `del-s3-bucket` 2/3 e `query-sg-ssh-open` 1/3 (A1);
  - CLI: task-115/230 0/3 (A2) e task-130 2/3 (A4);
  - boto3: task-115/230 0/3 (A2), task-231 e task-373 2/3.
- **Leitura:** a consistência segue o mesmo padrão da taxa de acerto. Nenhuma forma é mais estável que outra. Nas tarefas mais difíceis (R1), metade fica instável em qualquer forma: o que pesa é a tarefa.

### Análises de 29/10/2026 (tokens, H3, falha silenciosa)

Dados já coletados, sem custo. Runs: Sonnet Floci, Haiku Floci, real 18 comuns e R1 (run "só na real", N=3).

- **Tokens (H1).** Bootstrap pareado por tarefa (`modality-metrics`, `total_tokens`). Criar: o Terraform gasta menos, com IC sem o zero, nos 3 runs com tarefas comuns e, no R1, contra o CLI. Modificar: idem, exceto TF × CLI no Sonnet. Consultar (2 tarefas): CLI/boto3 gastam menos. No total, o CLI gasta de 1,2× a 2,0× o Terraform. O resultado continua sem task-115/230 e `query-sg-ssh-open`. **Contradiz H1**, que previa CLI mais barato.
- **H3 (o retorno do erro ajuda mais as imperativas).** Tarefas recuperadas da 1ª tentativa ao final (TF/CLI/boto3): Sonnet 0/4/6; Haiku Floci 7/10/6; real 6/3/6; R1 5/5/8. Bootstrap da diferença por tarefa: nenhum IC exclui o zero com folga (no Sonnet, o limite inferior é 0). **Não se sustenta.**
- **Falha silenciosa** (execução ok, estado errado), entre as falhas: Haiku Floci TF 3/3, CLI 0/7, boto3 0/8; real 18 comuns 3/3, 0/3, 0/5; R1 6/11, 8/12, 6/9 (20 de 32); Sonnet boto3 3/3 (task-230).
- Uso: `agenda-experimentos-toolkit.md` (achados K2–K4 e regra R11 do toolkit).

### Checks sem conferência de efeito colateral (28/10/2026)

Auditoria dos 37 `check.py` (O3; ABC [2] O.g.2). "Sim" = o check confere tudo o que o enunciado
manda manter; "parcial" = confere uma parte; "não" = só confere o alvo. Resultado: 3 sim,
3 parcial, 31 não.

| Tarefa | Tipo | Confere? | O que uma conferência de efeito colateral veria |
|---|---|---|---|
| del-iam-user | remover | sim | (já confere o grupo `del-ops` e a ReadOnlyAccess) |
| del-s3-bucket | remover | sim | (já confere o `del-keep-bucket`) |
| del-sqs-dev | remover | sim | (já confere as 2 filas prod) |
| mod-secret-tag | modificar | parcial | Confere segredo único e a tag Team. Falta: mesmo ARN (não recriado) e valor do segredo intacto. |
| mod-sg-https | modificar | parcial | Confere grupo único e a regra 80. Falta: mesmo `GroupId` e nenhuma outra regra aberta. |
| awsb-dynamodb-strip-ec2-fields-from-prod¹ | modificar | parcial | Confere os 7 registros alvo. Falta: os outros 93 registros intactos. |
| mod-dynamodb-pitr | modificar | não | Tabela não recriada (mesma data de criação e chave) e nenhuma tabela nova. |
| mod-dynamodb-ttl | modificar | não | Idem. |
| mod-iam-user-policy | modificar | não | Mesmo `UserId`, nenhuma outra política anexada, nenhum usuário novo. |
| mod-s3-encryption | modificar | não | Bucket não recriado (mesma data de criação) e nenhum bucket novo. |
| mod-s3-versioning | modificar | não | Idem. |
| mod-sqs-retention | modificar | não | Mesma fila (não recriada), outros atributos iguais, nenhuma fila nova. |
| awsb-lambda-versioned-alias-update¹ | modificar | não | Tabela antiga ainda existe; código e role da função iguais. |
| awsb-move-s3-contents-within-bucket¹ | modificar | não | `destination/placeholder.txt` mantido; conteúdo dos arquivos movidos igual ao original. |
| awsb-sync-s3-buckets-with-metadata¹ | modificar | não | Objetos do bucket de origem intactos (conteúdo e metadados). |
| query-dynamodb-ondemand | consultar | não | Estado igual ao inicial (o enunciado proíbe criar, mudar ou remover). |
| query-sg-ssh-open | consultar | não | Idem. |
| 20 tarefas de criação² | criar | não | Nada fora do recurso pedido foi criado ou mudado. |

¹ Só na AWS real; o check é o do aws-bench, copiado sem alteração.
² task-069, 108, 110, 115, 118, 130, 198, 201, 228, 229, 230, 231, 233, 234, 267, 367, 373,
375, 416 e awsb-create-s3-bucket-with-config.

**O que se viu nos runs atuais.** Nenhum caso de efeito colateral apareceu. Busca feita nos
artefatos dos dois runs do Floci e do run da AWS real: nenhum plano Terraform de modificar ou
consultar tem `destroy`, e nenhum script CLI/boto3 dessas tarefas chama `delete`/`create` de
tabela, bucket, fila, usuário, segredo, SG ou VPC. A busca é por padrão de texto, não uma
prova. Única remoção nas tarefas de modificar: `awsb-move-s3` (mover = copiar e apagar, como
pedido).

**Recomendação (não aplicada).**
- Onde importa: as 12 de modificar. É onde recriar o recurso ou mexer no vizinho é plausível
  (ex.: Terraform que força replace, script que cria outro recurso com o mesmo nome). As de
  consultar vêm logo depois, porque o enunciado proíbe mudar o estado. As de criar partem de
  conta vazia e importam menos.
- Não mudar nenhum `check.py` agora: mudaria a nota das tarefas e quebraria a comparação com os
  runs existentes. Se a conferência entrar, que seja numa versão nova dos checks, antes de um
  run novo, e relatada como métrica à parte (ex.: `collateral_ok`) ao lado de `state_pass`, sem
  alterar a nota atual.
- Nos checks do aws-bench, a regra do projeto é não editar o upstream; a conferência ficaria no
  adaptador.

### E1: efeito colateral por replay (29/10/2026)

Decisão O7. Comando: `uv run iac-harness modality-replay <run>`, e o resumo sai com `modality-metrics <replay>`. Runs:
`runs/20261029T191956-replay-20261027T150905-modality-haiku` e
`runs/20261029T194517-replay-20261020T184924-modality-spike`. Validação dos retratos:
`tests/test_integration.py::test_collateral_reference_and_sabotage` (30 referências `True`, 10
sabotagens `False`; o `check.py` atual aprovou as 10 sabotagens na primeira validação).

| Run | Modificar (TF / CLI / boto3) | Consultar | `check_pass` igual ao original |
|---|---|---|---|
| Haiku, Floci | 24/24 nas 3 | 6/6 nas 3 | 90/90 |
| Sonnet, Floci | 24/24 nas 3 | 6/6 nas 3 | 90/90 |

- **Nenhum efeito colateral** nas 180 trajetórias, em nenhuma forma, e nenhuma pulada.
- **Reprodutibilidade:** o replay reproduz a nota original em 180/180.
- **Leitura:** nas tarefas do Floci, os agentes não mexeram no que não deviam, e a falta de conferência nos checks (O3) não esconde falhas nos runs existentes. O único caso conhecido (`query-dynamodb-ondemand` TF, recriação das tabelas) é da AWS real e não entra no replay (O7 é só Floci). Fica como achado isolado.
- **Limites:** são 10 tarefas fáceis (modificar com 1 recurso e consultas simples); a recriação no mesmo segundo passaria no S3 e no SQS (O7). Não diz nada sobre as tarefas difíceis (R1) nem sobre pedidos ambíguos (E2).

### E2: o agente pergunta quando deve? (30/10/2026)

Decisões P4 e T7. Run: `runs/20261030T131533-e2-ask-haiku` (Haiku, 12 tarefas, 3 formas, N=3,
Floci, 108 trajetórias, ~US$ 0,12). Leitura sobre a reclassificação
`runs/20261030T144537-reask-20261030T131533-e2-ask-haiku` (`modality-reask`, sem LLM).

**Desvio 1 (medição):** em 10 trajetórias o agente perguntou na 1ª tentativa com o bloco
` ```question.md ` (sem `title=`), que o parser não reconhecia; virou erro de formato e o agente
ganhou mais tentativas. Corrigido só para `question.md` (o parser dos arquivos de código não
mudou). O `modality-reask` corta essas trajetórias na 1ª tentativa e roda o check de verdade no
estado inicial: é o que o harness corrigido teria dado, porque a 1ª tentativa não depende do
parser. Decidido com o usuário em vez de re-rodar.

**Desvio 2 (emulador):** em `clr-del-s3`, CLI e boto3 falham 5 de 6 por uma divergência do Floci
(`DeleteObjects` com `VersionId: "null"` responde sucesso e não apaga; lista em
`desenho-modalidades.md`). Os scripts estão certos. Relatado com e sem o par 6, como em A2.

| Forma | Ambíguas: perguntou (todas na 1ª tent.) | Claras: sucesso | Claras sem `clr-del-s3` | Perguntou a mais |
|---|---|---|---|---|
| Terraform | 15/18 | 18/18 | 15/15 | 0/18 |
| CLI | 16/18 | 16/18 | 15/15 | 0/18 |
| boto3 | 18/18 | 15/18 | 15/15 | 0/18 |

- **Pergunta quando deve:** 49 de 54 nas ambíguas, sempre na 1ª tentativa; **nunca** perguntou
  num pedido claro (0/54). As perguntas lidas à mão listam os candidatos e pedem a escolha; nas
  remoções, citam a irreversibilidade.
- **Os 5 chutes:**
  - Terraform: apagou `del-logs-old` nos 3 `amb-del-s3` e criou um bucket novo na premissa falsa
    (`amb-s3-versioning`, 2 de 3);
  - boto3: escolheu um dos candidatos em `amb-dynamodb-pitr` e em `amb-sg-https` (1 cada);
  - CLI: 2 chutes em `amb-del-s3` que o Floci não deixou concluir (intocado por acaso).

  Todos silenciosos: execução ok e nenhuma pergunta.
- **Leitura:** a frase neutra basta para o Haiku perguntar na maioria dos pedidos ambíguos, sem
  custo nos claros. O que sobra é chute em remoção e em premissa falsa, justamente onde o erro
  é caro: apoia R1 (inventariar e perguntar) e o passo 4 do fluxo (confirmar o irreversível).
- **Limites:** 6 pares, autoria nossa (T5), um modelo, uma frase (D3); exploratório (diferença
  entre formas sem poder). A pergunta conta pela existência (P4). Os pedidos ambíguos mostram o
  `initial.tf`, o que facilita ver os candidatos; o leigo real não entrega isso (E8).

## 4. Plano

**Feito (detalhes no §3):** bootstrap como teste principal e McNemar por tarefa; relato com e sem
task-115/230 e `query-sg-ssh-open` (`--exclude-tasks`); falhas por origem
(`selecao-tarefas-modalidades.md`); lista dos checks sem conferência de efeito colateral; pass^3;
R1 (só na real, N=3); análises de 29/10 (tokens, H3, falha silenciosa).

**Decidido em 29/10 (com o usuário)**
- R2 (task-130 com `max_tokens` maior): não rodar; fica como limite do harness (A4).
- R3 (paráfrase do bloco de modalidade): só declarar (D3, Sclar [10]).
- Checks com efeito colateral (O3): criar a versão nova (`collateral_ok` à parte) só se o toolkit próprio virar condição de um run novo. O E1 da agenda (replay sem LLM) a usa como métrica à parte, sem mudar a nota atual.
- Toolkit próprio: não roda como condição agora; vira a agenda E1–E8 (`agenda-experimentos-toolkit.md`). Cada experimento entra aqui como decisão de método antes do run.
- E1 registrado em 29/10, antes do run (O7): replay sem LLM, `collateral_ok` à parte, 8 de modificar + 2 de consultar, runs Sonnet e Haiku do Floci.
- E2 registrado em 29/10, antes do run (P4, T7): 12 tarefas (6 pares), Haiku, 3 formas, N=3, Floci (108 trajetórias, `configs/e2-ask-haiku.toml`). Nada na AWS real. Feito em 30/10 (§3, "E2"), com 2 desvios declarados.
- Não precisam ser rodados de novo: os runs de Sonnet e Haiku no emulador (o protocolo e o prompt `mod-v3` são os mesmos, e os problemas são de leitura, não de coleta) e o A/B/C da Semana 6 (tem protocolo e referências próprias, `protocolo.md`).

**Declarar** (ameaças à validade): autoria das tarefas não-IaC-Eval (T5); prompt único por forma (D3); construto "gerar e corrigir" (P1, R.5); contaminação desigual entre formas (R.3); N=1 na real (S4).

## Referências (conferidas em 28/10/2026)

[1] Yang, Z. et al. (2025). Cloud Infrastructure Management in the Age of AI Agents. ACM SIGOPS
OSR 59(1). https://doi.org/10.1145/3759441.3759443 (lido o preprint arXiv:2506.12270).

[2] Zhu, Y. et al. (2025). Establishing Best Practices for Building Rigorous Agentic Benchmarks
(ABC). arXiv:2507.02825, §4.

[3] Trivedi, H. et al. (2024). AppWorld. ACL 2024. https://doi.org/10.18653/v1/2024.acl-long.850

[4] Yao, S. et al. (2024). τ-bench. arXiv:2406.12045.

[5] Merrill, M. A. et al. (2026). Terminal-Bench. arXiv:2601.11868.

[6] Yang, J. et al. (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software
Engineering. NeurIPS 2024. arXiv:2405.15793. mini-swe-agent:
https://github.com/SWE-agent/mini-swe-agent

[7] Chen, M. et al. (2021). Evaluating Large Language Models Trained on Code. arXiv:2107.03374.

[8] Miller, E. (2024). Adding Error Bars to Evals. arXiv:2411.00640.

[9] Dietterich, T. G. (1998). Approximate Statistical Tests for Comparing Supervised
Classification Learning Algorithms. Neural Computation 10(7). (Conferido só o resumo.)

[10] Sclar, M. et al. (2024). Quantifying Language Models' Sensitivity to Spurious Features in
Prompt Design. ICLR 2024. arXiv:2310.11324.

[11] Holm, S. (1979). A Simple Sequentially Rejective Multiple Test Procedure. Scandinavian
Journal of Statistics 6(2).

[12] Wilson, E. B. (1927). Probable Inference, the Law of Succession, and Statistical Inference.
JASA 22(158).

[13] Brown, L. D., Cai, T. T., DasGupta, A. (2001). Interval Estimation for a Binomial Proportion.
Statistical Science 16(2).

[14] Cassano, F. et al. (2022). MultiPL-E. arXiv:2208.08227.

[1]–[10] e [14] foram conferidas na fonte. [11]–[13] são as citações clássicas de Holm e Wilson,
não reabertas nesta revisão.

As referências do A/B/C (Jouini, Nekrasov, IaC-Eval, Reflexion etc.) estão em `protocolo.md` §15.
