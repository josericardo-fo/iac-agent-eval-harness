# Handoff: fechar a v1 do harness

Data: 1º de outubro de 2026. Leia este arquivo inteiro antes de agir.

## Estado atual

O harness implementa o protocolo `docs/protocolo.md` (v3). Lint, format e os 61 testes unitários
passam. Os 2 testes de integração (`uv run pytest -m integration`) também passaram contra o Floci
real em 1º/10.

Mudanças desta sessão, todas ainda **não commitadas**, porque o repositório não tem nenhum commit:

- **Reset do sandbox** (`src/iac_harness/executors/floci.py`): `reset()` faz `docker restart` no
  container do Floci, espera o health e cria a VPC default nas 17 regiões comerciais.
  - Motivo: o `POST /_floci/state/reset` do Floci 2.1.0 apaga a VPC default e não a recria.
    Isso fazia ~17 referências de RDS falharem e daria feedback falso ao agente na condição C.
  - O seed precisa ir como form-urlencoded (`data=`). Com `content=` o Floci responde 204 e não
    cria nada.
- **Procedência** (`collect_provenance` em `src/iac_harness/cli.py`): registra git commit/dirty,
  ID da imagem da toolchain, providers do mirror, imagem e digest do Floci, revisão do dataset,
  `PROMPT_VERSION` e versão dos padrões de UNSUPPORTED. Vai para `run_config.json` (run) e
  `prevalidation_config.json` (prevalidate).
- **Versões fixadas:** o Floci em `docker/compose.yaml` (`floci/floci:2.1.0@sha256:f5aa8c18…`) e
  os providers em `docker/toolchain.Dockerfile` (aws 6.66.0, archive 2.8.1, random 3.9.1).
- **Métricas** (`src/iac_harness/metrics.py`): pass@k (k=3) a partir das tentativas 1, como
  controle de "mais tentativas sem feedback". É exploratório e fica fora do Holm.
- **Protocolo v3:** traz o reset, os defeitos do dataset, a ausência de harness oficial, a
  cobertura sem ajustar o emulador, o pass@k e as ameaças à validade (§14.1).

Decisão do usuário: **não ajustar o Floci para ganhar cobertura**. Nada de mock de MSK e nada de
montar o socket Docker para Redshift ou Lambda. O que não roda no emulador fica como limitação.

## Tarefas (nesta ordem)

### 1. Commit inicial

- O usuário já pediu este handoff para que o commit seja feito. Mesmo assim, **confirme com ele
  antes de rodar `git commit`**.
- Rode `uv run ruff check && uv run ruff format --check && uv run pytest` antes de commitar.
- Inclua tudo o que está untracked, **inclusive este `HANDOFF.md`**. O `git_dirty` da procedência
  usa `git status --porcelain`, então qualquer arquivo fora do commit marca a execução como suja.
  Remova este arquivo num commit separado depois da tarefa 3.
- `data/`, `runs/` e `.env` já estão no `.gitignore`. Confira com `git status` que nada sensível
  entra no commit.
- Branch: `main`, que é a primeira e ainda não tem commits.

### 2. Rebuild da imagem da toolchain

```bash
docker build -f docker/toolchain.Dockerfile -t iac-harness-toolchain:latest .
docker run --rm --entrypoint sh iac-harness-toolchain:latest \
  -c 'find /opt/terraform-providers -name "*.zip" | sed "s#.*/##"'
```

Critério: os zips precisam ser exatamente `terraform-provider-aws_6.66.0_*`,
`terraform-provider-archive_2.8.1_*` e `terraform-provider-random_3.9.1_*`. São as mesmas versões
da imagem anterior, então a troca não muda nenhum resultado.

Os ARGs dos providers são expandidos pelo heredoc interno `<<TF`, que não tem aspas. Esse build
ainda não foi testado. Se as versões não baterem, o problema está aí.

### 3. Pré-validação limpa das 186 tarefas

```bash
docker compose -f docker/compose.yaml up -d --wait
uv run iac-harness prevalidate --config configs/mvp.toml          # ~1h30; rode em background
```

**Checagem rápida antes**, em ~3 min:

```bash
uv run iac-harness prevalidate --config configs/mvp.toml \
  --tasks aws/task-218 aws/task-221 aws/task-269 aws/task-117
```

- Esperado: SANDBOX = PASS, PASS, PASS e FAIL. A 117 tem nome de bucket inválido fora de us-east-1.
- O `prevalidation_config.json` precisa ter a procedência preenchida, com `git_commit` não nulo e
  `git_dirty = false`.

**Critérios de aceitação** da execução completa, em `runs/<id>/prevalidation.jsonl` e
`prevalidation_summary.json`:

- 186 linhas e `git_dirty = false`.
- `harness_invalid_tasks` igual a exatamente `aws/task-129`, `aws/task-190` e `aws/task-360`.
  São defeitos do próprio dataset, explicados no protocolo §4.2.
- `aws/task-155`, `214` e `215` válidas (`harness_valid`), mas sem `sandbox_supported`.
- Com `sandbox_supported = true`: 216, 217, 218, 219, 221, 325, 326, 408, 409, 410, 411, 412, 415,
  416, 418, 419 e 420. São as tarefas de RDS que o reset novo recupera.
- Todo `ERROR` no SANDBOX é timeout de Redshift (140–143, 146–149) ou de MSK (209, 212).
- Cobertura do Floci esperada entre ~49% e 50%, ou seja, cerca de 92 de 186.

Se algum critério falhar, **não force o resultado**:

- investigue a tarefa isolada com `--tasks`;
- compare com o protocolo;
- reporte ao usuário.

Pode haver divergência legítima: o número esperado de cobertura foi estimado juntando execuções
parciais.

## Ao terminar

Reporte ao usuário:

- o run id;
- o resumo, com cobertura e a lista de `HARNESS_INVALID`;
- qualquer desvio em relação aos critérios acima.

Depois disso, remova este `HANDOFF.md` num commit separado, que precisa da autorização do usuário.

Fica fora deste handoff e é para decidir depois com o usuário:

- robustez de execução longa (retry/retomada, porque `max_retries=0` e falha de API derrubam o run);
- estratificar os testes pareados por `difficulty`;
- registrar o `stop_reason`;
- smoke ampliado antes do N=5.
