# Desenho: modalidade de execução × tipo de tarefa (rascunho, Semana 6)

Status: desenho, sem implementação. Ponto de partida para discutir no gate de 15/10/2026.

## 1. Motivação

Yang et al. (2025), *Cloud Infrastructure Management in the Age of AI Agents* (SIGOPS OSR 59(1)),
comparam quatro modalidades (SDK, CLI, Terraform, ClickOps) em VMs no Azure. Nenhuma modalidade
domina: CLI e IaC empatam no provisioning (SR 1,00), IaC cai em updates (0,33 contra 0,67 de SDK e
CLI) e em monitoring (0,40). Os autores propõem um agente que escolhe a modalidade por tarefa.

Esse resultado tem limitações que o próprio paper declara ou que a leitura identificou:

- 11 tarefas, só VMs, Azure, sem estatística;
- modelo diferente por modalidade (Copilot/GPT-4 × WorkArena/GPT-4o);
- prompts ajustados à mão por modalidade e tarefa;
- sem medição de custo nem de tokens.

O harness atual controla exatamente essas variáveis: o mesmo modelo, o mesmo prompt-base, o
ambiente com reset, o desenho pareado e os intervalos de Wilson. Por isso é um bom instrumento
para **replicar a pergunta com controle**.

## 2. Pergunta e hipóteses

**Pergunta.** Mantidos fixos o modelo, as tarefas e o orçamento de tentativas, a taxa de sucesso e
o custo de um agente de infraestrutura dependem da modalidade de execução (Terraform declarativo ×
AWS CLI imperativo × SDK boto3), e essa dependência muda com o tipo de tarefa (criação × modificação)?

- **H1 (criação).** Em tarefas de criação, Terraform e CLI têm sucesso semelhante, e o CLI gasta
  menos tokens (replicação de Yang et al.).
- **H2 (modificação).** Em tarefas de modificação de infraestrutura existente, a vantagem relativa
  muda. Em Yang et al., o IaC piora em updates. O mecanismo proposto, a perda de contexto e de
  estado, é testável aqui porque o agente recebe a config existente.
- **H3 (feedback).** O ganho do feedback de execução (condição C) é maior nas modalidades
  imperativas, onde não existe gate pré-execução equivalente a `validate` e `plan`.

## 3. Variáveis

| Tipo | Variável | Níveis |
|---|---|---|
| Independente | Modalidade | `terraform` (atual), `awscli`, `boto3` |
| Independente | Tipo de tarefa | criação (IaC-Eval v2), modificação (Multi-IaC-Eval) |
| Controlada | Modelo, temperatura, `max_tokens`, orçamento de tentativas, Floci com reset | fixos |
| Dependente | Sucesso pelo oráculo de estado, tokens, tentativas, classe de erro | — |

ClickOps fica fora porque o Floci não tem console. Monitoring também fica fora porque não há
tarefas nem oráculo para essa classe.

## 4. O problema central: um oráculo comum às modalidades

As policies Rego do IaC-Eval avaliam o **plan** do Terraform, e CLI e boto3 não produzem plan.
Comparar modalidades exige um oráculo que veja o **estado final do Floci**, independentemente de
como foi produzido:

1. Executar o artefato da modalidade no Floci limpo (`terraform apply`, script `aws ...`, script
   Python).
2. Ler o estado resultante via API (describe/list dos serviços envolvidos) e normalizar para um
   documento JSON por recurso.
3. Avaliar esse documento com uma policy de estado. A opção mais barata é derivá-la executando a
   **referência** Terraform e comparando o estado do agente com o estado da referência nos atributos
   que a tarefa pede.

**Risco principal.** A normalização por serviço é trabalho manual e limita a amostra aos serviços
mais comuns: S3, VPC/subnet/SG, IAM, SNS/SQS, DynamoDB e Lambda. Esses são justamente os mais
presentes nas tarefas executáveis e nas triplas compatíveis do Multi-IaC-Eval.

## 5. Mudanças no harness (estimativa)

- **Toolchain:** adicionar AWS CLI v2 e Python com boto3 fixados na imagem (hoje ela só tem
  Terraform e OPA).
- **Executor:** rodar scripts shell/Python com `AWS_ENDPOINT_URL` apontando para o Floci, com os
  mesmos limites de rede e timeout do apply.
- **Segurança:** scripts imperativos são código arbitrário. Manter a rede `internal`, credenciais
  falsas e sem acesso ao host, como hoje (protocolo §9.2), e acrescentar uma allowlist de comandos
  `aws`.
- **Prompt:** um prompt-base comum mais um bloco por modalidade, registrado em `prompt_version`.
  O *modality-aware prompting* de Yang et al. não tem ablação; aqui, o bloco fica mínimo e fixo.
- **Oráculo de estado** (§4) e um gate pré-execução por modalidade, se houver (ex.: `aws ... --dry-run`
  e `shellcheck`/`ruff` como análogos fracos do `validate`).

## 6. Sequência sugerida

1. Spike: 3 a 5 tarefas de S3/VPC, com as três modalidades e oráculo de estado escrito à mão.
   Serve para confirmar que o oráculo distingue sucesso de falha.
2. Oráculo de estado genérico para os serviços mais comuns e pré-validação das referências por
   estado.
3. Rodada de criação (IaC-Eval v2, tarefas executáveis) × 3 modalidades.
4. Rodada de modificação (Multi-IaC-Eval, triplas executáveis) × 3 modalidades. Na modalidade
   imperativa, o estado inicial é criado aplicando a config `initial`, e o agente recebe o
   Terraform inicial como descrição do ambiente.

## 7. Ameaças à validade

- O oráculo de estado pode ser mais permissivo ou mais estrito que o Rego. Medir a concordância
  entre os dois nas tarefas Terraform.
- Contaminação: o modelo pode ter visto muito mais Terraform que scripts boto3 para esses recursos.
- O Floci pode divergir entre APIs, por exemplo quando uma operação funciona via provider e não via
  CLI.
- O prompt por modalidade é uma variável confundida. Mitigar com blocos mínimos e simétricos.
