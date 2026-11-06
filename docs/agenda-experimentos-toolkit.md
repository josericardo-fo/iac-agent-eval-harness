# Agenda de experimentos e desenho do toolkit (Semana 9)

29/10/2026. Fonte do entregável da Semana 9. Cruza os achados do Estudo de Modalidades com os
artigos lidos na Residência e deriva: (1) as lacunas do que testamos; (2) os experimentos que
faltam; (3) o que o toolkit próprio deve ter. Números detalhados no `registro-metodologico.md`
(§3, incluindo as análises de 29/10). A dissecação do Agent Toolkit for AWS e as regras R1–R12 estão nos Anexos A e B.

## 1. Objetivo final

Um fluxo/skill que ajude um agente a **manter a nuvem de uma pessoa leiga**: criar, mudar,
consultar e remover recursos sem que ela precise dominar a AWS. Cada regra do toolkit precisa de
um achado que a justifique. O estudo responde em que casos usar cada forma (Terraform, AWS CLI,
boto3); esta agenda leva a pergunta do "qual forma" para o "qual comportamento".

## 2. O que já sabemos

| # | Achado | Evidência | Força |
|---|---|---|---|
| K1 | **Acerto igual entre as formas** em todos os tipos de caso. | 4 runs (Sonnet e Haiku; Floci e real; N=3); bootstrap e McNemar por tarefa; pass^3. | Forte (dentro destas tarefas) |
| K2 | **Custo: Terraform gasta menos tokens para criar e modificar; CLI/boto3 gastam menos para consultar.** No total, o CLI gasta de 1,2× a 2,0× o Terraform. | Bootstrap pareado de tokens por tarefa: criar com IC sem o zero nos 3 runs com tarefas comuns; modificar também, exceto TF × CLI no Sonnet. Continua sem task-115/230 e `query-sg-ssh-open`. No run R1 (difíceis), só criar TF × CLI. | Forte em criar/modificar; **fraca em consultar (2 tarefas)** |
| K3 | **O retorno do erro ajuda as 3 formas por igual** (H3 do desenho não se sustenta). | Tarefas recuperadas da 1ª tentativa ao final: Sonnet 0/4/6, Haiku Floci 7/10/6, real 6/3/6, run R1 5/5/8 (TF/CLI/boto3). Nenhum IC exclui o zero com folga; no Sonnet o limite inferior é exatamente 0. | Média |
| K4 | **Falha silenciosa** (executa sem erro, estado errado). Nas tarefas fáceis, só no Terraform; nas difíceis, na maioria das falhas de qualquer forma. | Haiku Floci: TF 3/3, CLI 0/7, boto3 0/8 das falhas; real 18 comuns: 3/3, 0/3, 0/5; run R1: 6/11, 8/12, 6/9 (20/32). Sonnet: boto3 3/3 (task-230, "nenhuma tabela encontrada" e sai com sucesso). | Forte |
| K5 | **O que derruba o agente é o pedido, não a forma:** premissa falsa (task-115/230, `awsb-move-s3`), efeito colateral em consulta (`query-dynamodb-ondemand`), trava de custo (task-416). | Registro, A2 e "Falhas por origem". | Forte |
| K6 | **O emulador reproduz a real** em 49 de 54 combinações; divergências pontuais e conhecidas. | Registro, "Emulador × AWS real". | Forte |
| K7 | **Na real aparecem problemas que o emulador esconde:** nomes globais, consistência eventual, serviços fechados a contas novas. | `datasets.md`; O5; `selecao-tarefas-modalidades.md`. | Forte |

**Leitura conjunta.** K3 e K4 se explicam juntos: o laço de correção só age quando há erro, e as
falhas que importam são silenciosas. Logo, o que o toolkit precisa acrescentar não é "mais
tentativas", e sim **conferir o estado e o sentido do pedido**.

## 3. Cruzamento com a literatura lida

| Trabalho | O que diz | O que nossos dados mostram | Implicação |
|---|---|---|---|
| Yang et al. 2025, *Cloud Infrastructure Management in the Age of AI Agents* | Nenhuma modalidade domina; CLI com menos passos para criar; IaC simplifica updates declarativos, mas foi pior neles (perda de contexto e estado). Propõe guardrails, humano no laço, rollback, memória de workflows. | Confirma "nenhuma domina" com controle (mesmo modelo, prompt, estatística). Na eficiência, a métrica é outra (passos lá, tokens aqui): em tokens, o Terraform gasta menos para criar e modificar (K2). IaC não piora em updates (modificar 24/24 nas 3 formas, real), porque o agente recebe a config e o state. | A escolha da forma vira regra de **custo**, não de acerto. O roadmap deles vira experimentos (E2, E4, E6). |
| Yang et al. 2025, NSync | Reconciliar drift (mudança fora do IaC) com ferramentas próprias (`drift_report`, `self_critique`) e memória por projeto; tirar o `drift_report` derruba pass@3 de 0,98 para 0,60. | Não testamos drift. | Lacuna L4 → E7. Para o toolkit: ferramenta específica vale mais que instrução genérica. |
| Jouini 2026, *Verifier-First* | validate → plan → OPA; 79% das falhas OPA eram falta de informação no prompt. | Conferência do OPA (S7): nas 13 tarefas, a regra que falha não estava no enunciado. Falha silenciosa passa em qualquer verificador de execução (K4). | Conferir o **estado final**, não só os verificadores (R4). |
| Nekrasov et al. 2026, *Error Taxonomy / Knowledge Injection* | Injetar conhecimento corrige a parte técnica, mas a intenção trava em ~73–75% (*Correctness-Congruence Gap*); mais contexto pode piorar tarefas simples (*Optimal Context*). | As falhas que sobram são de intenção (K5), não de sintaxe. | O toolkit deve tratar **intenção** (perguntar, inventariar), não só despejar conhecimento. Medir dano nas tarefas simples (E3). |
| Jana et al. 2026, TerraFormer | Gerar e mutar são tarefas distintas; o reparo de mutações piora com a complexidade. | Modificar fácil: 24/24; modificar difícil (run R1): 6–8 de 12. | Lacuna L3: mutações de vários recursos → E5. |
| Qiu et al. 2024, Zodiac | Sintaxe ≠ semântica ≠ implantável; restrições da nuvem são opacas e mudam. | K6 e K7: o que passa no emulador pode falhar na real (bucket global, serviço fechado). | Ensaiar no emulador e **saber onde ele diverge** (R10); a lista de divergências é conhecimento do toolkit. |
| Khan et al. 2025, MACOG | Validadores, sandbox plan/apply, planejador de custo; tirar o sandbox é o maior corte (números marcados como provisórios na v1). | Sandbox (Floci) funcionou como base (K6). | Reforça R10; custo antes de agir (R7). Evidência fraca. |
| Yang et al. 2024, SWE-agent | A interface (ACI) muda o resultado: ações compactas, feedback conciso, guardrails. | O protocolo atual só dá "script + erro". | O toolkit deve oferecer **ferramentas** (inventário, conferência com espera), não só regras → E3 compara regra × ferramenta. |
| Toprani e Madisetti 2025 | Fluxo recuperar → detectar → relatar para segurança de IaC (evidência fraca). | Não testamos auditoria. | Consultas de auditoria entram na lacuna L1. |
| Rahman et al. 2019, *Where Are the Gaps?* | Faltavam estudos empíricos de defeitos e segurança em IaC. | Nosso estudo é empírico, com origem das falhas. | Contexto histórico. |
| Yao et al. 2024, τ-bench (método) | Usuário simulado; pass^k mede consistência. | pass^3 já relatado. | **Usuário simulado** permite medir "perguntou ao usuário" (E2). |
| SkillsBench 2026 (citado no Planejamento S8; não lido por completo) | Skills curadas: +16 pp em média, +4,5 pp em engenharia de software; 16 de 84 tarefas pioram. | — | Hipótese a testar, não base: medir a piora nas tarefas de controle (E3). Ler antes de citar no relatório. |

## 4. Lacunas do que testamos (tarefas que faltam)

| # | Lacuna | Hoje | Por que importa para o leigo | Tarefas a criar |
|---|---|---|---|---|
| L1 | **Consultar** | 2 tarefas | É o pedido mais comum ("o que tenho?", "quanto gasto?", "o que está público?"). K2 em consulta se apoia em 2 tarefas. | 6–8: recurso por tag, bucket público, chave IAM antiga, SG aberto em várias VPCs, inventário com paginação (>1 página), várias regiões. |
| L2 | **Remover** | 3 tarefas (Floci e real) | Limpar é onde o leigo perde dinheiro e onde o erro é irreversível. | 4–5: bucket com versões, VPC com dependências, "apague tudo do projeto X" por tag, remover com vizinho parecido (dev × prod). |
| L3 | **Modificar com vários recursos** | Modificar fácil tem 1 recurso | TerraFormer: a dificuldade cresce com a complexidade. | 3–4: trocar a classe de um RDS mantendo os dados, mudar política usada por 2 roles, renomear com dependentes. |
| L4 | **Drift** (mudança fora do IaC) | Nenhuma | O leigo mexe no console; o agente volta depois. NSync mostra que é outra tarefa. | 2–3 só em Terraform: o initial aplica e depois um script muda o recurso por fora. |
| L5 | **Pedido ambíguo de propósito** | Por acaso (task-115/230, `move-s3`) | O leigo descreve mal. Hoje não há resposta certa "perguntar". | Pares claro × ambíguo derivados das tarefas existentes (E2). |
| L6 | **Pedido em linguagem leiga** | Enunciados técnicos (nomes, ARNs) | Validade do construto: o produto é para quem não sabe o nome do recurso. | Paráfrase leiga de 8–10 tarefas existentes, mesmo check (E8). |
| L7 | **Ciclo de vida** (criar → mudar → consultar → remover na mesma conta) | Tarefas isoladas | Manter é sequência; o Terraform guarda state e as imperativas redescobrem a cada passo (Yang et al.). | 2–3 roteiros de 4 passos (E6). |

As tarefas novas **não alteram as instâncias existentes** (decisão do dataset): são tarefas novas,
derivadas, com o mesmo gate (o initial reprova e a referência passa nas 3 formas). A autoria nossa
entra na ameaça T5.

## 5. Experimentos

Cada experimento só roda depois de registrado no `registro-metodologico.md` (decisão de método
**antes** do run). Custos de API estimados pelos runs do Haiku (o run completo de 225 trajetórias
custou cerca de US$ 0,40).

| # | Pergunta | Base | Desenho | Custo | Prazo | O que muda no toolkit |
|---|---|---|---|---|---|---|
| E1 | Os agentes mexeram no que não deviam? | O3 do registro; ABC O.g.2; `query-dynamodb-ondemand` | **Replay** dos scripts finais já gerados no Floci, com checks novos de efeito colateral (`collateral_ok`, métrica à parte). Sem LLM. | US$ 0 | 1 dia | Mede quanto R3 (consulta read-only) e R9 (inventário) importam. **Feito em 29/10:** 0 efeitos colaterais em 180 trajetórias (Haiku e Sonnet, 3 formas). Nas tarefas fáceis, R3 e R9 não têm base no dado do Floci; a única base é o caso isolado da AWS real (`query-dynamodb-ondemand`). Registro, §3, "E1". |
| E2 | O agente pergunta quando deve, e só quando deve? | K5; Nekrasov (intenção); τ-bench (usuário); regras *starter* da AWS | 6–8 pares claro × ambíguo (L5). Desfecho novo: o agente pode devolver `question.md` em vez de agir. No ambíguo, o certo é perguntar; no claro, agir (perguntar demais conta como erro). Haiku, 3 formas, N=3, Floci. | < US$ 1 | 2 dias (inclui o desfecho no harness) | Base de R1/R2 e da regra de "nível de ajuda". **Feito em 30/10:** nas ambíguas, perguntou em 49/54 (TF 15, CLI 16, boto3 18 de 18), sempre na 1ª tentativa; nas claras, 0/54 perguntas a mais. Os 5 chutes foram silenciosos e caíram em remoção e premissa falsa. Registro, §3, "E2". |
| E3 | O toolkit ajuda ou atrapalha? | SkillsBench; Nekrasov (*Optimal Context*); SWE-agent (interface) | Haiku sem toolkit × regras do `aws-core` × toolkit próprio (regras) × toolkit próprio (regras + ferramentas). Tarefas: as de E2 + as sensíveis (K5) + 10 de controle das 25. Uma forma (a de menor custo por tipo, K2). As regras do `aws-core` citam ferramentas MCP que o harness não oferece: a condição é uma adaptação. Nas tarefas de controle o Haiku já está perto do teto. | ~US$ 1–2 | 2–3 dias, depois de E1 e E2 | Decide o que fica em regra e o que vira ferramenta; mede a piora nas fáceis. **Mudou em 06/11:** agente real (Claude Code headless em container), 5 condições (+ `own-skill-only`), Floci 35 tarefas × Haiku/Sonnet × N=3, depois a real com 30 (registro P6/T8). Piloto: sem a regra no system prompt a skill não é acionada (0/3 × 6/6). Matriz pendente; a real espera a AWS liberar o Lambda. |
| E4 | Se o agente escolhe a forma, ele escolhe a mais barata? | Yang et al. (agente que escolhe); questão do gate S6 | Condição "forma livre" nas 25 tarefas do Floci; mede a escolha e o custo contra K2. | < US$ 0,50 | 1 dia | Decide se a regra de custo precisa ser explícita. **Feito em 30/10:** sem dica, a 1ª escolha seguiu K2 em 61/66 trajetórias (TF para criar 31/36 e modificar 24/24, CLI para consultar 6/6) e foi a mais barata em 18/25 tarefas; tokens de saída 1,19× a forma mais barata e 0,44× a mais cara; acerto 72/75 (task-230, premissa falsa). R11 não precisa ser explícita para criar/modificar. Registro, §3, "E4". |
| E5 | K2 em consulta e K1 em remover se mantêm com mais tarefas? | L1, L2, L3 | 12–15 tarefas novas, nas 3 formas, N=3, Floci; confirmação só leitura na real. | < US$ 1 (+ centavos na AWS) | 2 dias | Firma ou derruba a regra "CLI/SDK para consultar". |
| E6 | No ciclo de vida, o state do Terraform dá vantagem? | Yang et al. (contexto e estado); L7 | 2–3 roteiros de 4 passos na mesma conta; nota por passo e no fim. | < US$ 1 | 3 dias | Regra de forma para manutenção contínua. |
| E7 | O agente reconcilia drift? | NSync; L4 | 2–3 tarefas de drift em Terraform, com e sem ferramenta de diff. | < US$ 0,50 | 2 dias | Ferramenta "o que mudou por fora". |
| E8 | O pedido leigo derruba o acerto? | L6; Sclar (sensibilidade ao prompt); R.5 do ABC | Paráfrase leiga de 8–10 tarefas, mesmo check, pareado com a técnica. | < US$ 0,50 | 1 dia | Mede o tamanho do problema que o toolkit precisa resolver. |

**Ordem proposta até o gate (≥ 04/11):** E1 → E2 → E4 → E3 (piloto). E5 em paralelo se sobrar
tempo (só escrever tarefas). E6, E7 e E8 ficam para a Semana 10 ou como trabalho futuro.

**Riscos.** Poder baixo (poucas tarefas por família): tudo de E2 a E8 é exploratório. O desfecho
"perguntar" muda o protocolo; ele precisa valer igual nas 3 formas e não pode virar fuga (por isso
o par claro conta perguntar como erro). Autoria das tarefas novas (T5).

## 6. O que o toolkit deve ter (v0.2)

Mesmas três camadas do Agent Toolkit for AWS, com status de evidência: **[A]** achado nosso;
**[E]** emprestado da AWS, sem achado nosso; **[H]** hipótese, testada pelo experimento indicado.

**Fluxo da skill (para cada pedido)**
1. **Entender.** Reescreva o pedido em uma frase. Se ele cita um recurso ("a tabela", "o site"),
   **inventarie antes**. Se não achar ou houver mais de um, pergunte [A: K5, R1; H: E2].
2. **Classificar o caso** e escolher a forma pelo custo: consultar → CLI/SDK só leitura; criar e
   modificar → Terraform; remover → o que criou o recurso [A: K2, fraca em consultar; remover: H, E5; escolha livre: A, E4 (o agente já escolhe assim)].
3. **Ensaiar** no emulador quando o serviço é suportado, conferindo a lista de divergências [A: K6, K7].
4. **Confirmar** com o usuário o que é irreversível ou custa dinheiro, em linguagem simples [E: regras *starter*; H: E2].
5. **Agir** com credencial mínima e tag em tudo que criar [A: R8, R9].
6. **Conferir o estado** com espera da consistência eventual. "Rodou sem erro e nada mudou" é
   suspeito [A: K4, R2, R4, R5].
7. **Relatar** o que mudou, comparando o inventário com o de antes, e perguntar se deve apagar o
   que não serve [A: R9; E: regras *starter*].

**Regras (sempre no contexto, curtas):** R1–R12 do Anexo B (R11 corrigida em 29/10: a
forma se escolhe pelo custo, não pelo acerto).

**Ferramentas (scripts que o agente chama)** [H: E3]
- `inventory`: lista recursos por tag/região, com paginação, e salva um baseline;
- `verify`: confere um atributo com repetição até ~60 s;
- `diff-baseline`: o que apareceu ou sumiu desde o baseline;
- `whoami`: conta, região e permissões ativas antes de agir.

**Guardrails (fora do agente):** credencial do agente com boundary e budget; perfil só leitura
para consultas; tag obrigatória [A: registro E3/E4; E: IAM do toolkit da AWS].

**Fica de fora, por falta de base:** forma escolhida pelo acerto (K1); "carregue a skill sempre"
(SkillsBench, Nekrasov; testado em E3); ClickOps e monitoramento (fora do escopo).

## Anexo A. O Agent Toolkit for AWS, dissecado

Fonte: `github.com/aws/agent-toolkit-for-aws`, commit `96a27f2` (29/10/2026), plugin `aws-core`
v1.2.0, e o guia do usuário em `docs.aws.amazon.com/agent-toolkit`. Lido sem instalar nada.

**Onde fica cada conhecimento**

| Camada | O que é | O que carrega |
|---|---|---|
| Regras (`rules/aws-agent-rules.md`, 55 linhas) | Texto sempre no contexto | Comportamento geral: buscar a skill antes de qualquer tarefa AWS; conferir na documentação em vez de adivinhar; não dizer "não existe" sem varrer todas as páginas e regiões; descrever o que roda (deploy, alias), não o rascunho; causa só como confirmada se houver evidência; preferir IaC (CDK/CloudFormation) ao CLI para criar; segredos nunca no contexto. |
| Regras "starter" (`aws-starter-rules.md`) | Para contas novas com login social | Vocabulário para leigos ("projeto", não "conta"); uma região só; limite de gasto; nível de ajuda (LOW/MEDIUM/HIGH) que define quantas perguntas o agente faz; perguntar se o usuário quer apagar o que foi criado. |
| Skills (25 no `aws-core`) | Carregadas sob demanda (`retrieve_skill`) | Conhecimento de serviço: "correções verificadas de coisas que agentes erram" (ex.: `aws-iam`), roteamento para a skill certa (`aws-database`, `aws-networking`), CDK/CloudFormation. **Nenhuma trata de Terraform** como forma de trabalho; ele só aparece como entrada (ex.: gerar política a partir de um `plan` JSON). |
| Ferramentas (AWS MCP Server, gerenciado) | `run_script` (Python em sandbox, com acesso às APIs), `search_documentation`, `read_documentation`, `retrieve_skill`, `get_regional_availability`, `get_presigned_url`, `get_tasks` | Execução e documentação atual. O README ainda cita `call_aws`; a lista de ferramentas do guia já não. |
| Hook (Claude Code) | `secret-safety.py` no `PreToolUse` | Bloqueia `get-secret-value` no Bash, no SDK e no MCP. Único guardrail que roda no cliente. |

**Como o toolkit limita o agente**
- **IAM com chaves só de agente:** `aws:ViaAWSMCPService` e `aws:CalledViaAWSMCP` deixam negar,
  por exemplo, `s3:DeleteBucket` só quando a chamada vem do MCP.
- **Auditoria:** no CloudTrail, `invokedBy`/`userAgent` = `aws-mcp.amazonaws.com` (data events).
- **Limite conhecido:** se o agente roda `aws` ou boto3 pelo Bash, a chamada não passa pelo MCP e
  essas chaves não se aplicam. A própria AWS recomenda permissão mínima e boundary, que valem para
  os dois caminhos (Konishi, 2026; guia, "Identity-based policy examples").
- **Read-only:** não há modo read-only no toolkit. Só existe por política IAM.
- **Sandbox do `run_script`:** a documentação não diz o que fica isolado (rede, disco, tempo).

## Anexo B. Regras do toolkit (R1–R12)

| # | Regra | Achado que a justifica | Evidência | No toolkit da AWS? |
|---|---|---|---|---|
| R1 | **Antes de criar, confira se o recurso citado já existe.** Se o pedido fala de "a tabela", "o bucket X" e nada é achado, pare e pergunte; não crie nem desista calado. | Diante de premissa falsa, o Terraform cria e passa, CLI e boto3 procuram e falham. Nenhuma das duas reações é a certa para um leigo: criar pode duplicar o que ele achava que existia. | A2; task-230 repete o padrão na real (3/3 TF, 0/3 CLI e boto3). | Em parte: "antes de concluir que não existe, olhe" (*Before you conclude*). Não manda perguntar. |
| R2 | **"Rodou sem erro, mas não achou nada" é suspeito.** Relate como resultado parcial e diga onde procurou. | O mesmo de R1, do lado da consulta/modificação: o script termina com sucesso e o estado não muda. | A2; `awsb-move-s3` 0/9 (caminho errado de propósito no enunciado). | Sim: "diga que o resultado é parcial e o que conferiu". |
| R3 | **Consultar nunca muda o estado.** Use só leitura (CLI/SDK `describe`/`list`, ou `data` no Terraform), e garanta isso com uma credencial read-only, não com a instrução. | Numa consulta, o agente recriou as 4 tabelas como `resource` em vez de lê-las. | `query-dynamodb-ondemand` TF, rep 2, AWS real (registro, "Emulador × AWS real"). E1: 0 de 180 no Floci; é raro, mas irreversível, e a regra se justifica pelo custo do erro, não pela frequência. | Não como regra. O IAM por chave de MCP não cobre o caminho pelo Bash (Anexo A). |
| R4 | **Confira o estado final na API depois de agir**, não só o código de saída ou o `plan`. | A nota que importa é o estado; validadores estáticos erram nos dois sentidos. | O1; Conferência do OPA (S7): nas 13 tarefas, a regra que falha não estava no enunciado; `success` × `state_pass` (O6). | Em parte: "descreva o que roda, não o rascunho". |
| R5 | **Na conferência, espere a consistência eventual**: repita a leitura por até ~60 s antes de declarar falha. | Na AWS real, a leitura logo após a mudança reprovou soluções certas. | O5; `del-sqs-dev` e `mod-secret-tag`. | Só em skills específicas (ex.: propagação de IAM no Bedrock). |
| R6 | **Nomes globais (S3) levam um sufixo único** (ex.: id da conta) e nunca nomes de exemplo. | 4 tarefas do IaC-Eval usam nomes de bucket que já pertencem a outras contas e não passam na real. | `datasets.md`: task-108, 110, 115, 118. | Não. |
| R7 | **Bloqueio de permissão é uma trava, não um erro a contornar.** Diante de `AccessDenied`, explique ao usuário, proponha a opção mais barata e não tente outro caminho para escapar da trava. | A trava de custo barrou a task-416; só passou quando o agente escolheu uma classe micro. | Registro E4; run R1 (task-416, 1/9). | Em parte: a regra *starter* manda perguntar pelo limite de gasto ao ver `AccessDenied`. |
| R8 | **Credencial do agente com permissão mínima e boundary**, separada da do dono; o dono confere. | O harness só roda como `iac-harness` (política escopada); o root fica para leitura e conferência. Funcionou nos 3 runs do agente na real. | Registro E3/E4; `runs/aws-baseline.json`. | Sim, como recomendação de IAM, com o limite do Anexo A. |
| R9 | **Tudo que o agente cria leva tag; ao terminar, compare o inventário da conta com um baseline** e mostre o que sobrou. | A limpeza deixou NAT gateway e Elastic IP (pagos) até o sweep ser corrigido. | `dec16eb`; regra pós-run R1: inventário com o root depois de cada run. | Em parte: a regra *starter* manda perguntar se o usuário quer apagar o que foi criado. |
| R10 | **Teste no emulador primeiro, sabendo onde ele diverge**; confirme na real só leitura. | O emulador reproduz a real em 49 de 54 combinações; as divergências conhecidas são pontuais. | Registro E1/E3 e A1 (`ip-permission.*` ignorado); task-108 (`expected_bucket_owner`). | Não (o toolkit só fala da conta real). |
| R11 | **Escolha a forma pelo custo:** Terraform para criar e modificar; CLI/SDK só leitura para consultar. O acerto é igual. | Acerto igual em todos os runs (K1); tokens diferem por tipo de caso (K2). | Registro, §3 ("Análises de 29/10"); §2 desta agenda. Consulta com 2 tarefas: fraca (E5). | Diverge em parte: a AWS manda preferir IaC para criar, sem falar de custo. |
| R12 | **Segredo nunca entra no contexto do agente.** | Sem achado próprio. O harness nunca imprime a chave (`IAC_AWS_*`). | — | Sim: hook `secret-safety.py`. Emprestada. |

### Comparação com o toolkit da AWS

- **Onde convergem:** não concluir "não existe" sem procurar (R1, R2); conferir o que roda de fato
  (R4); permissão mínima (R8); segredos (R12).
- **Onde o toolkit próprio acrescenta, com evidência:** perguntar diante de premissa falsa (R1);
  consulta garantida read-only (R3); consistência eventual na conferência (R5); nomes globais (R6);
  inventário contra baseline (R9); emulador antes da real (R10).
- **Onde diverge:** a AWS prefere IaC para criar; os dados não mostram vantagem de acerto, só de custo (R11).
- **Onde a AWS vai além e nós não temos dado:** documentação ao vivo, skills por serviço e o nível
  de ajuda para leigos. A regra "carregue a skill sempre, mesmo se souber" tem custo de contexto;
  SkillsBench (2026) mostra ganho pequeno em engenharia de software e piora em 16 de 84 tarefas.

## Referências

Artigos lidos (Base Literatura Residência): Rahman et al. 2019; Qiu et al. 2024 (Zodiac); Jouini
2026; Nekrasov et al. 2026; Jana et al. 2026 (TerraFormer); Toprani e Madisetti 2025; Yang et al.
2025 (Cloud Management); Khan et al. 2025 (MACOG); Yang et al. 2025 (NSync); Yang et al. 2024
(SWE-agent). Método: ABC, τ-bench, Miller, Sclar (lista completa no registro). SkillsBench (2026)
citado no Planejamento da Semana 8, não lido por completo. Agent Toolkit for AWS: repositório
`aws/agent-toolkit-for-aws`, commit `96a27f2`.
