# Ideia: demonstração animada da nuvem subindo (para explorar depois)

Registrado em 06/11/2026. Não é trabalho da Residência antes do gate S10. Pelo combinado com o
professor, gráficos e apresentação feitos a partir do que já existe podem vir depois do
congelamento. Esta demo entra nessa categoria: **não roda experimento novo, só reproduz o que já
foi gravado**.

## O que é

Uma animação que mostra, passo a passo, o agente montando a infraestrutura: cada recurso aparece
como um nó (bucket, fila, tabela, VPC, security group, função) e as ligações entre eles surgem
como arestas (a função lê a fila, o SG fica dentro da VPC, a policy dá acesso ao bucket). Ao lado,
uma legenda curta diz o que o agente fez naquele momento ("listou a conta", "rodou terraform plan",
"perguntou ao usuário", "conferiu o estado").

**Para quê:** apresentação do gate e do TCC, e a cara do produto. Mostra ao leigo o que o agente
fez na conta dele, que é a promessa do toolkit (passo 7 da skill, "Report").

## Por que é viável sem gerar dado novo

Cada trajetória gravada já tem o necessário:

| Fonte (no run dir) | Dá o quê |
|---|---|
| `transcript.jsonl` (E3, stream do Claude Code) | Sequência e horário de cada chamada de ferramenta (Bash, Skill, Write) e o texto final |
| `work/` e `terraform/attempt_N/terraform.tfstate` | Recursos criados, com atributos e dependências (`depends_on`, referências entre recursos) |
| `initial/` / `initial.tf` | O que já existia antes (aparece "aceso" desde o início) |
| `events.jsonl` | Desfecho: check, colateral, perguntou, tokens, duração |
| `inventory.py` / `diff_baseline.py` (own-tools) | Antes × depois da conta, sem depender de Terraform |

## Desenho em três peças

1. **Extrator (Python, no harness):** lê um run dir e produz uma linha do tempo em JSON:
   `[{t, kind: "create|modify|delete|read|ask|verify", resource: {type, id, label}, edges: [...],
   caption}]`. Os recursos vêm do tfstate quando há Terraform. Nas formas CLI/boto3, vêm do
   parse dos comandos (`aws sqs create-queue ...`) ou do diff de inventário. As arestas vêm das
   referências no state (ARN de um recurso dentro de outro).
2. **Player (uma página HTML estática):** grafo com layout por grupos (rede, dados, computação,
   identidade) e animação no ritmo da linha do tempo, com play/pause e velocidade. Candidatos:
   D3 force layout, Cytoscape.js ou React Flow; escolher na hora, sem dependência no harness.
   Ícones: o pacote oficial *AWS Architecture Icons* (conferir a licença antes de usar na
   apresentação).
3. **Roteiro da apresentação:** 2–3 trajetórias escolhidas a dedo:
   - uma criação com várias peças ligadas (ex.: Lambda + fila + role; ou um stack do E6, se
     existir);
   - o **pedido ambíguo** em que o agente lista a conta e **pergunta** em vez de apagar (E2 e o
     piloto do E3), o achado que mais conversa com o leigo;
   - lado a lado, `none` × `own-tools` na mesma tarefa: o toolkit faz o inventário antes e a
     conferência depois.

## Variações (se sobrar fôlego)

- **Modo "conta do leigo":** a mesma animação narrada em linguagem simples, com o custo mensal
  de cada nó (preço público) e um "como desfazer" no fim.
- **Floci × AWS real** da mesma tarefa, lado a lado (validade externa, visual).
- **Exportar vídeo** (gravação da página com Playwright) para o slide, sem depender de rede ao
  vivo.

## Cuidados

- Redigir segredos: o `stream.py` já redige. O extrator deve reaproveitar essa redação, não
  ler o transcript cru.
- Não mostrar o account id real na apresentação (mascarar `931493484341`).
- Não confundir com resultado: a demo ilustra trajetórias escolhidas; os números vêm do registro.

## Esforço estimado

Extrator: ~1 dia. Player básico: ~1–2 dias. Roteiro e vídeo: ~meio dia. Total de 3–4 dias,
depois do gate S10.
