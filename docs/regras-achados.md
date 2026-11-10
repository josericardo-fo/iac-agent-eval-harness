# Regras do toolkit × achados (rastreabilidade)

10/11/2026. Preparado para o gate S9 (12/11). O objetivo do toolkit é que **cada regra seja
justificada por um achado**. Este documento liga cada regra do texto que o agente recebe
(`toolkit/src/RULES.md` e `toolkit/src/SKILL.md`) ao achado que a apoia, com o número e a seção
de origem. Não traz número novo: tudo vem do `registro-metodologico.md` (§1 e §3) e da
`agenda-experimentos-toolkit.md` (§2, §6 e Anexo B).

**Como ler.**

- **Base:** **[A]** achado nosso; **[E]** emprestado das regras da AWS (*starter* ou `aws-core`)
  ou da literatura, sem achado nosso; **[H]** hipótese ainda não testada.
- **R1–R12** são as regras do Anexo B da agenda. "Run só-real" é o run de 29/10 só na AWS real
  com N=3 (no registro, "R1"; outro R1, não a regra).
- **E3 Floci** mede o toolkit inteiro, não regra por regra. Só aparece efeito quando dá para
  ligá-lo a uma regra.
- **E3 real** (`20261109T163420-e3-agent-aws`, 290 trajetórias, N=1): ainda rodando; nada dele
  entra aqui.
- **Veredito:** **mantém**; **ajustar** (o texto ou o rótulo precisa mudar); **sem base**
  (candidata a sair ou a ser marcada como emprestada).

## 1. Texto entregue × Anexo B

O texto entregue não é igual ao Anexo B. Diferenças:

- **"Carregue a skill sempre"** está na 1ª linha do `RULES.md`. A agenda (§6) a punha em "fica de
  fora, por falta de base". O E3 deu a base depois (linha 1 da tabela). A agenda está desatualizada
  aqui.
- **Calibrar ao nível da pessoa** (skill, "Calibrate to the person") e **confirmar o irreversível
  ou caro** (passo 4; 4ª linha do `RULES.md`) não têm número no Anexo B. Estão só no fluxo da §6.
- **R6** no Anexo B diz "nomes globais levam sufixo único". O texto entregue foi corrigido em 06/11:
  "use os nomes exatos do usuário; sufixo só quando você escolhe o nome". O Anexo B ficou com a
  versão antiga.
- **R3** no Anexo B pede garantir a consulta "com uma credencial read-only, não com a instrução".
  O texto entregue só diz que a consulta não muda a conta. Não achei perfil só leitura no harness
  do E3 (a conferir).
- **R10** no Anexo B: "teste no emulador primeiro". O texto entregue diz "se houver emulador" e
  acrescenta **ler o `terraform plan` antes do `apply`**, que não está no Anexo B.
- **R11** no Anexo B: "CLI/SDK só leitura para consultar". O texto entregue também aceita `data` do
  Terraform, e acrescenta "remover com o que criou" e "editar o Terraform que já existe".
- **R2** no texto entregue ganhou "leia todas as páginas e regiões", vindo da regra *Before you
  conclude* da AWS.
- **Ferramentas** (`whoami`, `inventory`, `verify`, `diff_baseline`) só existem na condição
  `own-tools`.
- **Versão:** a matriz do Floci rodou com o passo 5 antigo (registro §3, "Correção do toolkit"). A
  real foi lançada em 09/11, depois da correção; que ela usa o texto novo é provável, mas está
  a conferir.

## 2. Tabela principal

| # | Regra (texto entregue, resumido) | Onde | Base | Achado e números | Efeito no E3 Floci | E3 real | Veredito |
|---|---|---|---|---|---|---|---|
| 1 | Carregue a skill `iac-cloud` em todo pedido de nuvem, mesmo simples. | `RULES.md` 1 | [A] | Piloto de 06/11: skill acionada em 6/6 com a regra no system prompt e em 0/3 sem ela (registro §3, "E3: pilotos"). Contra: a agenda (§6) citava SkillsBench e Nekrasov sobre o custo de contexto. | Haiku: skill acionada 30/105 sem a regra × 105/105 com ela; ambíguas 13/18 × 18/18. Sonnet aciona sozinho (99/105). Custo: +19–33% de tokens (toolkit inteiro). Sucesso total: nenhuma condição difere de `none`. | pendente | mantém (atualizar a agenda §6) |
| 2 | Calibre ao nível da pessoa: pedido preciso → seja breve; pedido vago → palavras simples e explique o passo caro ou irreversível. | skill, "Calibrate" | [E] | Nível de ajuda das regras *starter* da AWS (Anexo A). Sem achado nosso: o E8 (pedido leigo) não rodou. Indício indireto: a dificuldade do IaC-Eval não prevê nada; "o que pesa é o enunciado vago" (registro §3, "Por estrato"). | Sem efeito isolável. | pendente | sem base (marcar como emprestada) |
| 3 | Se o pedido cita algo que já deveria existir, inventarie antes. Se nada ou mais de um bate, pergunte. (R1) | `RULES.md` 2; passo 1 | [A] | A2: 12 das 15 falhas de CLI/boto3 em criar vêm de task-115/230 (premissa falsa); o Terraform cria, as imperativas falham. Na real, task-230: TF 3/3, CLI e boto3 0/3. E2: perguntou em 49/54 ambíguas, 0/54 a mais nas claras; os 5 chutes caíram em remoção e premissa falsa (registro §3, A2, "Emulador × AWS real", E2). | Ambíguas: 18/18 com a skill acionada × 10–12/18 em `none` e `aws-core`. Modificar: 42 de 42 em `own-rules` e `own-tools` × 35 (Haiku) e 34 (Sonnet) de 42 em `none`. Custo: perguntas a mais nas claras (Haiku 5 e 6 × 1; Sonnet 3 × 0), task-231 e task-373. Premissa falsa (3 no Floci) não melhora: Haiku 5 / 1 / 3 / 3 / 3 de 9, Sonnet 3 / 3 / 5 / 3 / 4 de 9 (em `none` / `aws-core` / `own-skill-only` / `own-rules` / `own-tools`) (a conferir qual resposta conta como certa nesse estrato). | pendente | mantém; ajustar o critério para pedido vago sem recurso (ver §3) |
| 4 | Escolha a forma pelo custo: consultar só leitura; criar e modificar com Terraform; remover com o que criou. O acerto é igual. (R11) | passo 2 | [A] (remover: [H]) | K1: acerto igual em todos os runs. K2: Terraform gasta menos para criar e modificar; o CLI gasta de 1,2× a 2,0× o Terraform no total; consultar com 2 tarefas, fraca. Remover: hipótese, E5 não rodou. E4: sem dica, a 1ª escolha segue K2 em 61/66 trajetórias e é a mais barata em 18/25 tarefas (registro §3, "Análises de 29/10" e E4). | Sem efeito isolável (o agente do E3 escolhe a forma livremente). | pendente | ajustar: E4 mostra que a regra não precisa ser explícita para criar/modificar; remover é [H] |
| 5 | Consultar deixa a conta exatamente como estava. (R3) | `RULES.md` 3; passo 2 | [A] | Um caso: `query-dynamodb-ondemand` TF, rep 2, AWS real, o agente recriou as 4 tabelas como `resource`. E1: 0 efeitos colaterais em 180 trajetórias no Floci. Raro, mas irreversível (registro §3, "Emulador × AWS real" e E1). | Colateral 30/30 em todas as células; consultar 6 em todas. Sem diferença (teto). | pendente | mantém (pelo custo do erro, não pela frequência); falta a credencial só leitura do Anexo B |
| 6 | Leia o `terraform plan` antes de todo `apply` e confira que só toca o pedido. | passo 3 | [E] | Sem achado nosso. Vem do fluxo validate → plan de Jouini (2026). O caso de `query-dynamodb-ondemand` (recriar como `resource`) apareceria num plano, mas isso não foi medido. | Sem efeito isolável. | pendente | sem base própria (marcar como emprestada) |
| 7 | Se houver emulador que suporte o serviço, ensaie nele, lembrando que ele diverge em detalhes; confirme na real só leitura. (R10) | passo 3 | [A] (como comportamento do agente: [H]) | K6: o emulador reproduz a real em 49 de 54 combinações. Divergências conhecidas: filtros `ip-permission.*` (A1), `DeleteObjects` com `VersionId: "null"` (E2, desvio 2). | Não testado: nenhuma condição deu ao agente emulador e real juntos. | pendente | ajustar: o achado apoia o método (Floci como base), não o comportamento; a lista de divergências não está no texto |
| 8 | Antes de ação irreversível ou paga que o usuário não pediu exatamente, pergunte em uma frase simples. | `RULES.md` 4; passo 4 | [E] (indício [A]) | Regras *starter* da AWS. Indício nosso no E2: os chutes caíram em remoção e premissa falsa, "onde o erro é caro"; as perguntas lidas à mão citam a irreversibilidade (registro §3, E2). Não está no Anexo B. | Piloto: no `amb-del-s3`, as 5 condições perguntaram sem apagar (inclusive `none`). Remover: 20 ou 21 de 21 em todas as células. Sem efeito isolável. | pendente | mantém; dar número no Anexo B e rotular [E] |
| 9 | Use a credencial recebida: mínima e separada da do dono. (R8) | passo 5 | [A] | O harness só roda como `iac-harness` (política escopada); o root fica para leitura. Funcionou nos 3 runs do agente na real (agenda, Anexo B). É guardrail de fora, não comportamento do agente. | Não se aplica (Floci). | pendente | mantém, como guardrail |
| 10 | Tag em tudo que criar; no fim, compare com o início e pergunte se algo deve ser apagado. (R9) | passo 5; passo 7 | [A] (fraco) + [E] | A limpeza deixou NAT gateway e Elastic IP (pagos) até o sweep ser corrigido (`dec16eb`). O achado é sobre a limpeza do harness; a tag como remédio não foi testada. "Perguntar se apaga" vem das regras *starter*. | Sem efeito isolável. | pendente | ajustar: rotular a tag como [H] e o "perguntar se apaga" como [E] |
| 11 | Use os nomes exatos do usuário; sufixo único só quando você escolhe o nome. (R6, corrigida) | passo 5 | [A] | `datasets.md`: task-108, 110, 115 e 118 usam nomes de bucket de outras contas e não passam na real. | Defeito do texto antigo: task-118 0/9 com a skill nos dois modelos, 3/3 sem. Depois da correção, 0/18 → 18/18 (só task-118; a matriz não foi re-rodada). | pendente | mantém (já ajustada); atualizar o Anexo B |
| 12 | `AccessDenied` é trava: explique e ofereça a opção mais barata dentro dela. (R7) | `RULES.md` 6; passo 5 | [A] | Run só-real: task-416, 1/9; só passou quando o agente escolheu uma classe micro (registro §3, "R1"; decisão E4). Se a regra muda o comportamento não foi testado. | Não testado: a task-416 (`cost-lock`) só existe na real. | pendente | mantém |
| 13 | Segredo fora da saída e do contexto; cite pelo nome ou ARN. (R12) | passo 5 | [E] | Sem achado próprio. Hook `secret-safety.py` da AWS. O harness nunca imprime a chave. | Não medido. | pendente | sem base própria (manter como emprestada) |
| 14 | Confira o estado final na API; código de saída ou `plan` não é prova. (R4) | `RULES.md` 5; passo 6 | [A] | K4: falha silenciosa (execução ok, estado errado). Haiku Floci: TF 3/3, CLI 0/7, boto3 0/8 das falhas; run só-real: 20 de 32. Conferência do OPA (S7): nas 13 tarefas, a regra que falha não estava no enunciado (agenda §2 e §3; registro §3, "Análises de 29/10"). | Piloto: `own-tools` chamou `verify.py` em 2/3. Na matriz, sem efeito isolável. | pendente | mantém |
| 15 | Repita a leitura por até ~60 s antes de declarar falha (consistência eventual). (R5) | passo 6 | [A] | O5: na real, a leitura logo depois da mudança reprovou soluções certas (`del-sqs-dev`, `mod-secret-tag`; agenda, Anexo B). | Sem efeito isolável no Floci. | pendente | mantém; só a real pode mostrar efeito |
| 16 | "Rodou sem erro, mas nada mudou ou nada achou" é suspeito: leia todas as páginas e regiões, ou relate como parcial e diga onde procurou. (R2) | passo 6 | [A] + [E] | K4 (números da linha 14). `awsb-move-s3` 0/9, caminho errado de propósito no enunciado (registro §3, "R1"). "Todas as páginas e regiões" vem da AWS (*Before you conclude*). | Sem efeito isolável. | pendente | mantém |
| 17 | Ferramentas: `whoami`, `inventory` (baseline), `verify` (espera até 60 s), `diff_baseline`. | `TOOLS.md` (`own-tools`) | [H] | Hipótese do E3 (agenda §6; SWE-agent: a interface muda o resultado). | `own-tools` × `own-rules`: Haiku ambíguas 18/18 nos dois, claras 69/78 × 70/78, tokens 232k × 211k; Sonnet claras 72/78 nos dois, 161k × 155k. Nenhum ganho visível, mais tokens. | pendente | sem base por ora; decidir com a real |

**Contagem.** 17 linhas (16 regras e as ferramentas). Base principal: 12 [A], 4 [E], 1 [H]. Três
das [A] têm parte não testada: a 4 (remover), a 7 (o agente ensaiar) e a 10 (a tag).

## 3. Achados sem regra

Achados que o texto entregue ainda não reflete:

- **Pedido vago sem recurso a procurar.** Na task-231 ("configure um TTL", sem tabela) e na
  task-373 (disco Lightsail, sem instância; só Haiku), o toolkit fez o agente perguntar em vez de
  criar (registro §3, "E3: matriz"). O passo 1 só fala de algo "que já deveria existir". Falta dizer
  quando criar com valores padrão e quando perguntar. A pergunta é defensável; o estrato `clear`
  dessas tarefas foi mantido (T8).
- **Premissa falsa não melhora no Floci.** O estrato tem 3 tarefas e o toolkit não ganha de `none`
  (linha 3). A regra R1 foi escrita para esse caso (A2). A conferir: na task-115/230 a resposta
  certa é criar ou perguntar.
- **Lista de divergências do emulador.** A agenda (§3 e §6) diz que ela é conhecimento do toolkit.
  O texto entregue só diz "emulators diverge on some details", sem a lista (A1; E2, desvio 2).
- **Prompt curto.** E4: a distância entre a razão total (1,33×) e a de saída (1,19×) vem do prompt
  de sistema com 3 blocos. "O que a regra pode acrescentar é menos o 'qual forma' e mais o prompt
  curto" (registro §3, E4). O toolkit não trata do tamanho do contexto; ele soma +19–33% de tokens.
- **Terraform sem recurso nativo.** No run só-real, o Terraform usou `local-exec` em 6 trajetórias,
  nas tarefas do aws-bench sem recurso nativo. O passo 2 manda Terraform para criar e modificar
  sem dizer o que fazer nesse caso.
- **Serviço fechado em conta nova (K7).** Não há regra própria; a linha 12 (`AccessDenied`) cobre
  em parte.

Achado que justifica uma **ausência**: K3 (o retorno do erro ajuda as 3 formas por igual) e K4
mostram que "mais tentativas" não resolve; o toolkit não tem essa regra, e está certo (agenda §2).

## 4. Regras sem achado

- **Calibrar ao nível da pessoa** (linha 2): emprestada da AWS; o E8 não rodou.
- **Ler o `plan` antes do `apply`** (linha 6): literatura (Jouini), sem medida nossa.
- **Segredo fora do contexto** (linha 13): emprestada; sem achado.
- **Ferramentas** (linha 17): hipótese; no Floci, sem ganho e com mais tokens.
- **Partes de regras com achado:** "remover com o que criou" (linha 4, [H], E5); "editar o
  Terraform que já existe" (linha 4); tag em tudo (linha 10); "o que agora custa dinheiro" no
  relato (passo 7); "todas as páginas e regiões" (linha 16, [E]).

## 5. Limites

- **N=1 na real.** A matriz real é exploratória (S4, E6) e ainda não terminou.
- **Juiz conferido por outro modelo, não por humano.** `asked` 30/30 e `kind` 29/30 entre dois
  modelos da mesma família; erros comuns aos dois não aparecem.
- **Estratos de autoria nossa** (T8), sem segundo avaliador; tarefas autorais (T5).
- **Um agente** (Claude Code), dois modelos, uma redação de cada regra (D3).
- **Floci.** Os números do E3 são do emulador; consistência eventual, nomes globais e trava de custo
  só aparecem na real.
- **Toolkit inteiro, não regra por regra.** O E3 compara condições; o efeito de cada regra é
  inferido pelas tarefas onde a diferença aparece.
- **Matriz do Floci com o texto antigo do passo 5.** A correção foi verificada só na task-118.
