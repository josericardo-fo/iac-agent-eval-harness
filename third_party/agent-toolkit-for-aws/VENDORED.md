# Agent Toolkit for AWS (vendorizado)

- **Origem:** https://github.com/aws/agent-toolkit-for-aws
- **Commit:** `96a27f273291299666bcc004c7b19f27e7d9986a` (`96a27f2`, 29/10/2026)
- **Copiado em:** 30/10/2026
- **Licença:** Apache-2.0 (`LICENSE`), com o `NOTICE` do upstream. A licença permite
  redistribuir cópias não modificadas desde que a licença e o NOTICE as acompanhem.

## O que foi copiado

| Aqui | No upstream |
|---|---|
| `LICENSE`, `NOTICE` | raiz |
| `plugins/aws-core/` | `plugins/aws-core/` (plugin inteiro, v1.2.0) |
| `rules/aws-agent-rules.md` | `rules/aws-agent-rules.md` |

**Nada foi modificado.** Os arquivos são cópias byte a byte (`diff -r` contra o checkout do
commit). O `pyproject.toml` exclui `third_party/` do ruff para que o código não seja
reformatado.

## Adaptação declarada

A configuração do AWS MCP Server (`plugins/aws-core/.mcp.json` e `mcp.json`) foi mantida como
está, mas não funciona offline: ela aponta para o servidor gerenciado em
`aws-mcp.us-east-1.api.aws`, e o harness não o fornece. As skills e o hook `secret-safety.py`
continuam carregando normalmente.
