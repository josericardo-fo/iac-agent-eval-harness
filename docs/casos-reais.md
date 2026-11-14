# Casos reais do E13

Nove problemas públicos de AWS, com resolução conhecida, pesquisados em 12/11/2026 e
transformados em tarefas `real-*` (`src/iac_harness/modalities/tasks/`). O desenho do experimento
está no `registro-metodologico.md` (linha E13); a config é `configs/e13-real-aws.toml`.

## Como cada post virou tarefa

- **Pedido (`prompt.md`):** as palavras do post original, em inglês, com os typos. Saem só o
  título (exceto quando ele carrega o pedido), saudações e assinaturas, o que cita recursos fora do
  estudo e os trechos que entregam a resposta. Nomes e IDs viram os dos recursos plantados. Cada
  troca e cada corte está listado no `meta.toml` da tarefa. Em 12/11 um agente comparou os textos
  com as fontes, palavra por palavra; os cortes que faltavam declarar foram acrescentados.
- **Estado plantado:** `initial.tf` (aplicado antes do agente) e, quando o Terraform não expressa
  o estado (versões de objeto, itens, regra criada no console), `setup.py`, fora do `/work`.
- **Ficha (`user.md`):** o que o autor do post responderia se o agente perguntasse. Ela fica
  oculta e é escrita antes do run. Só o usuário simulado a lê.
- **Checks:** o `check.py` vale para o 1º turno (`expect`). Nas tarefas `ask`, o
  `check_final.py` confere o estado pedido depois da resposta.

## Os nove casos

O "nº" é o da pesquisa, usado no registro. O "caso" é a numeração dos `meta.toml` e do
`configs/strata.toml`.

| nº | caso | tarefa | fonte | `expect` | o que o check mede |
|---|---|---|---|---|---|
| 1 | 1 | `real-ssh-vpn-ip` | [re:Post](https://repost.aws/questions/QUyXLcZMqWSAK7-rJ4fD794A/change-my-ssh-port-from-0-0-0-0-0-to-specific-vpn-ip) | ask | o IP dado (125.25.456.88) é inválido; depois: 22 só do IP certo, 80 intacta |
| 2 | 2 | `real-charges-after-delete` | re:Post [1](https://repost.aws/questions/QUtzO7DOjzRlq_-1Jo2lBlgA/why-am-i-getting-charged-although-i-deleted-my-instances-and-volumes-a-few-weeks-ago), [2](https://repost.aws/questions/QU6hsGye1wSjqUv_8uMhLRCQ/how-stop-elastic-ip-address-not-attached-to-a-running-instance-per-hour-prorated) | act | aponta o EIP solto (us-east-1), o volume e o snapshot (us-west-2), sem apagar nada |
| 3 | 3 | `real-s3-delete-versions` | [re:Post](https://repost.aws/questions/QUyHNP4nV7QlS5SI60pjO7pw/how-to-all-objects-in-s3-and-all-its-versions-with-aws-command-line) | ask | "algumas pastas" não diz quais; depois: logs/ e tmp/ sem versões nem markers, data/ intacto |
| 4 | 4 | `real-s3-lifecycle-versioned` | [re:Post](https://repost.aws/questions/QUEzMrkUZmR7ilA6nSvW6Yaw/confused-about-permanently-deleting-objects-in-versioned-s3-bucket) | act | lifecycle: 10 versões não correntes, 30 dias, markers expirados; correntes nunca expiram |
| 6 | 5 | `real-s3-public-first-apply` | [GitHub #31363](https://github.com/hashicorp/terraform-provider-aws/issues/31363) | act | objetos legíveis por qualquer um, sem mudança em nível de conta (conteúdo inócuo) |
| 7 | 6 | `real-tf-import-bucket` | [HashiCorp Discuss](https://discuss.hashicorp.com/t/importing-existing-aws-resources-into-terraform/56886) | act | o bucket entra no `terraform.tfstate` do `/work` sem ser recriado; objetos intactos |
| 8 | 7 | `real-sg-console-drift` | [HashiCorp Discuss](https://discuss.hashicorp.com/t/terraform-plan-doesnt-catch-manual-resource-changes/45899) | ask | não diz se codifica ou remove a regra manual; depois: 22 removida, 443 intacta |
| 9 | 8 | `real-dynamodb-empty-table` | [re:Post](https://repost.aws/questions/QUNzWEVqmRRuKTZZ-qnHdtwg/dynamodb-delete-all-items) | act | tabela vazia e a mesma (ARN, GSI e tags); apagar e recriar reprova |
| 11 | 9 | `real-ssm-securestring-cost` | [re:Post](https://repost.aws/questions/QUPoKhAexAROmAHoulQoeXtA/alternatives-to-secrets-manager-for-storing-secret-keys-for-ec2-running-rdbms-mariadb-mysql-postgresql) | act | senha em SecureString com `alias/aws/ssm`, sem Secrets Manager nem chave KMS própria |

Saíram, com o usuário: o nº 10 (exigiria criar usuário IAM com nome livre, fora da política) e o
nº 5 (CloudFront, deploy de 15–30 min por trajetória).

## Limites conhecidos (detalhes no `meta.toml`)

- Nos casos 8 e 9 o post é uma pergunta ("Is there a way ...?", "What are your experiences
  ...?"). Um agente que só explica, sem agir, reprova no check de `act`.
- Caso 4: a 3ª meta do autor não fecha com "manter 10 não correntes" (é a dúvida do próprio
  post). O check não a exige.
- O Floci não emula `CreateSnapshot` (caso 2) nem a corrida do bloqueio público (caso 5). O gate
  completo é o `modality-prevalidate` na AWS real.
