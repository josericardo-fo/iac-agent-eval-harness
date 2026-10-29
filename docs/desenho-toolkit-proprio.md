# Desenho do toolkit próprio (F3)

29/10/2026. Objetivo: um fluxo/skill que ajude agentes a manter a nuvem de pessoas leigas. Cada
regra aponta para um achado da Residência. Quando a regra vem do toolkit da AWS sem achado próprio,
isso é dito. Os números estão em `registro-metodologico.md` (§3); aqui só os cito.

## 1. O Agent Toolkit for AWS, dissecado

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

## 2. O que os achados da Residência dizem ao toolkit

Resumo dos achados que sustentam as regras (registro, §3):
- **Nenhuma forma (Terraform, CLI, boto3) é melhor em nenhum tipo de caso**: Floci com Sonnet e
  Haiku, AWS real nas 18 comuns e nas 12 só na real, com N=3. pass^3 também não separa.
- **O que pesa é a tarefa e o comportamento do agente**, não a forma: premissa falsa no enunciado
  (task-115/230, `awsb-move-s3`), efeito colateral numa consulta (`query-dynamodb-ondemand`),
  trava de custo (task-416), nomes globais (buckets de outras contas), consistência eventual.

Consequência direta: a regra "escolha a modalidade pelo tipo de caso", prevista no planejamento,
**não tem base** e sai do desenho. A regra da AWS "prefira IaC para criar" também não tem base no
critério de acerto; se ficar, é por outro motivo (rastreio e remoção do que foi criado).

## 3. Regras do toolkit próprio

| # | Regra | Achado que a justifica | Evidência | No toolkit da AWS? |
|---|---|---|---|---|
| R1 | **Antes de criar, confira se o recurso citado já existe.** Se o pedido fala de "a tabela", "o bucket X" e nada é achado, pare e pergunte; não crie nem desista calado. | Diante de premissa falsa, o Terraform cria e passa, CLI e boto3 procuram e falham. Nenhuma das duas reações é a certa para um leigo: criar pode duplicar o que ele achava que existia. | A2; task-230 repete o padrão na real (3/3 TF, 0/3 CLI e boto3). | Em parte: "antes de concluir que não existe, olhe" (*Before you conclude*). Não manda perguntar. |
| R2 | **"Rodou sem erro, mas não achou nada" é suspeito.** Relate como resultado parcial e diga onde procurou. | O mesmo de R1, do lado da consulta/modificação: o script termina com sucesso e o estado não muda. | A2; `awsb-move-s3` 0/9 (caminho errado de propósito no enunciado). | Sim: "diga que o resultado é parcial e o que conferiu". |
| R3 | **Consultar nunca muda o estado.** Use só leitura (CLI/SDK `describe`/`list`, ou `data` no Terraform), e garanta isso com uma credencial read-only, não com a instrução. | Numa consulta, o agente recriou as 4 tabelas como `resource` em vez de lê-las. | `query-dynamodb-ondemand` TF, rep 2, AWS real (registro, "Emulador × AWS real"). | Não como regra. O IAM por chave de MCP não cobre o caminho pelo Bash (§1). |
| R4 | **Confira o estado final na API depois de agir**, não só o código de saída ou o `plan`. | A nota que importa é o estado; validadores estáticos erram nos dois sentidos. | O1; Conferência do OPA (S7): nas 13 tarefas, a regra que falha não estava no enunciado; `success` × `state_pass` (O6). | Em parte: "descreva o que roda, não o rascunho". |
| R5 | **Na conferência, espere a consistência eventual**: repita a leitura por até ~60 s antes de declarar falha. | Na AWS real, a leitura logo após a mudança reprovou soluções certas. | O5; `del-sqs-dev` e `mod-secret-tag`. | Só em skills específicas (ex.: propagação de IAM no Bedrock). |
| R6 | **Nomes globais (S3) levam um sufixo único** (ex.: id da conta) e nunca nomes de exemplo. | 4 tarefas do IaC-Eval usam nomes de bucket que já pertencem a outras contas e não passam na real. | `datasets.md`: task-108, 110, 115, 118. | Não. |
| R7 | **Bloqueio de permissão é uma trava, não um erro a contornar.** Diante de `AccessDenied`, explique ao usuário, proponha a opção mais barata e não tente outro caminho para escapar da trava. | A trava de custo barrou a task-416; só passou quando o agente escolheu uma classe micro. | E4; R1 (task-416, 1/9). | Em parte: a regra *starter* manda perguntar pelo limite de gasto ao ver `AccessDenied`. |
| R8 | **Credencial do agente com permissão mínima e boundary**, separada da do dono; o dono confere. | O harness só roda como `iac-harness` (política escopada); o root fica para leitura e conferência. Funcionou nos 3 runs do agente na real. | E3/E4; `runs/aws-baseline.json`. | Sim, como recomendação de IAM, com o limite do §1. |
| R9 | **Tudo que o agente cria leva tag; ao terminar, compare o inventário da conta com um baseline** e mostre o que sobrou. | A limpeza deixou NAT gateway e Elastic IP (pagos) até o sweep ser corrigido. | `dec16eb`; regra pós-R1: inventário com o root depois de cada run. | Em parte: a regra *starter* manda perguntar se o usuário quer apagar o que foi criado. |
| R10 | **Teste no emulador primeiro, sabendo onde ele diverge**; confirme na real só leitura. | O emulador reproduz a real em 49 de 54 combinações; as divergências conhecidas são pontuais. | E1/E3; A1 (`ip-permission.*` ignorado); task-108 (`expected_bucket_owner`). | Não (o toolkit só fala da conta real). |
| R11 | **A forma (Terraform, CLI, SDK) fica a critério do contexto do usuário** (o que ele já usa, se precisa remover tudo depois). Não há forma melhor em acerto. | Nenhuma diferença significativa em nenhum run. | Registro, §3 (bootstrap e McNemar por tarefa; pass^3). | Diverge: a AWS manda preferir IaC para criar. |
| R12 | **Segredo nunca entra no contexto do agente.** | Sem achado próprio. O harness nunca imprime a chave (`IAC_AWS_*`). | — | Sim: hook `secret-safety.py`. Emprestada. |

## 4. Comparação com o toolkit da AWS

- **Onde convergem:** não concluir "não existe" sem procurar (R1, R2); conferir o que roda de fato
  (R4); permissão mínima (R8); segredos (R12).
- **Onde o toolkit próprio acrescenta, com evidência:** perguntar diante de premissa falsa (R1);
  consulta garantida read-only (R3); consistência eventual na conferência (R5); nomes globais (R6);
  inventário contra baseline (R9); emulador antes da real (R10).
- **Onde diverge:** a AWS prefere IaC para criar; os dados não mostram vantagem de acerto (R11).
- **Onde a AWS vai além e nós não temos dado:** documentação ao vivo, skills por serviço e o nível
  de ajuda para leigos. A regra "carregue a skill sempre, mesmo se souber" tem custo de contexto;
  SkillsBench (2026) mostra ganho pequeno em engenharia de software e piora em 16 de 84 tarefas.

## 5. Vale virar condição do experimento?

Proposta (decidir com o usuário; se sim, registrar a decisão no registro **antes** do run):
- **Condições:** Haiku sem skill × com as regras do `aws-core` × com o toolkit próprio, mesma forma
  e mesmas tarefas.
- **Onde pode haver diferença:** só nas tarefas em que o comportamento pesou (task-115/230,
  `awsb-move-s3`, `query-dynamodb-ondemand`, task-416). Nas outras o Haiku já está perto do teto.
- **O que exige antes:** (a) medir "perguntou ao usuário" como desfecho, porque hoje a pergunta não
  tem para onde ir no protocolo; (b) a versão nova dos checks com `collateral_ok` (§4 do registro);
  (c) as regras da AWS citam ferramentas MCP que o harness não oferece, então seria uma adaptação.
- **Risco:** com 5 tarefas sensíveis e N=3, o poder é baixo; o resultado seria exploratório.
