# iac-agent-eval-harness

Harness para medir o ganho incremental de feedback (validate, plan, OPA e sandbox Floci) no loop de
um agente que gera Terraform, usando o IaC-Eval v2. O protocolo completo está em
[docs/protocolo.md](docs/protocolo.md).

## Estado

Harness v1: dataset IaC-Eval v2, backend Anthropic, toolchain Docker, verificadores (validate, plan,
OPA), executor Floci com reset por restart, procedência registrada (git, imagens, providers) e CLI
(`prevalidate`, `run`, `metrics`, com controle pass@k).

## Uso

```bash
uv sync
uv run ruff check && uv run ruff format --check
uv run pytest                  # exclui testes de integração por padrão
uv run pytest -m integration   # sobe o compose; exige Docker e a imagem da toolchain
```

### 1. Construir a imagem da toolchain

```bash
docker build -f docker/toolchain.Dockerfile -t iac-harness-toolchain:latest .
```

A imagem traz Terraform (1.15.x exato, `ARG TERRAFORM_VERSION`), OPA 1.16.1 e um filesystem mirror
com os providers fixados (`aws` 6.66.0, `archive` 2.8.1 e `random` 3.9.1, via `ARG`), então
`terraform init` funciona sem internet.

### 2. Subir o Floci

```bash
docker compose -f docker/compose.yaml up -d --wait
docker compose -f docker/compose.yaml down   # ao terminar
```

O Floci fica na rede `iac-sandbox` (`internal: true`); a porta 4566 é exposta só em 127.0.0.1.
A imagem do Floci está fixada por digest. A toolchain acessa `http://floci:4566`; o harness, no host,
usa `sandbox.host_endpoint` (127.0.0.1).

O reset é um `docker restart` do container `sandbox.container`, seguido de espera pelo health e da
criação da VPC default em todas as regiões. O `POST /_floci/state/reset` do Floci 2.1.0 não serve:
apaga a VPC default e não a recria (protocolo §9.2). Se o reset falhar, o comando aborta.

Chaves de `[sandbox]`: `endpoint` (visão da toolchain), `host_endpoint` (visão do host, padrão
`http://127.0.0.1:4566`), `container` (padrão `iac-harness-floci-1`) e `region`.

### 3. Pré-validar as referências

```bash
uv run iac-harness prevalidate --config configs/smoke.toml --limit 3
uv run iac-harness prevalidate --config configs/mvp.toml --tasks aws/task-269
```

Grava `runs/<id>/prevalidation.jsonl` (status por estágio, `harness_valid`, `sandbox_supported`) e
`prevalidation_summary.json` (Floci coverage), mais `prevalidation_config.json` com a procedência. O dataset é baixado para `data/` na primeira vez.

### 4. Rodar o agente

```bash
export ANTHROPIC_API_KEY=...   # a CLI não lê .env: exporte (ex.: set -a; source .env)
uv run iac-harness run --config configs/smoke.toml --tasks aws/task-269 --reps 1 \
    --prevalidation runs/<id>/prevalidation.jsonl   # ou --no-prevalidation (smoke)
```

Grava `runs/<id>/events.jsonl` e `run_config.json` (config, procedência, `prompt_version` e o sha256
da pré-validação). `--prevalidation`
é obrigatório; só `--no-prevalidation` (smoke) permite rodar sem ela, assumindo `harness_valid` e
`sandbox_supported` verdadeiros.

### 5. Métricas

```bash
uv run iac-harness metrics runs/<id>
```

Para rodar sem Docker (dev), use `toolchain.mode = "local"` com `terraform` e `opa` no PATH; o
endpoint do Floci deve então ser alcançável do host (por exemplo `http://127.0.0.1:4566`).

A chave da Anthropic vem da variável de ambiente (modelo em `.env.example`).
