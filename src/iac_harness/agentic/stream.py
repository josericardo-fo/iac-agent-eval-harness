"""Leitura do stream-json do `claude -p --output-format stream-json --verbose`.

Eventos usados: `system/init` (plugins e ferramentas carregados), mensagens `assistant` com blocos
`tool_use` (chamadas de ferramenta) e o `result` final (texto, usage, custo, turnos).
"""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedStream:
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # {"name", "input"}

    @property
    def final_text(self) -> str | None:
        return self.result.get("result") if self.result else None

    @property
    def usage(self) -> dict[str, Any]:
        return dict(self.result.get("usage") or {}) if self.result else {}

    @property
    def cost_usd(self) -> float | None:
        return self.result.get("total_cost_usd") if self.result else None

    @property
    def num_turns(self) -> int | None:
        return self.result.get("num_turns") if self.result else None

    @property
    def is_error(self) -> bool | None:
        return self.result.get("is_error") if self.result else None

    @property
    def subtype(self) -> str | None:
        return self.result.get("subtype") if self.result else None

    @property
    def permission_denials(self) -> list[Any]:
        return list(self.result.get("permission_denials") or []) if self.result else []

    @property
    def tool_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(c["name"] for c in self.tool_calls).items()))


# Valores menores que isto não são filtrados (ex.: "test" das credenciais do Floci).
MIN_SECRET_LEN = 8
REDACTED = "***"


def redact(text: str, secrets: Iterable[str | None]) -> str:
    """Troca cada valor secreto por `***` (o token do gateway fica visível dentro do container)."""
    for value in sorted({s for s in secrets if s and len(s) >= MIN_SECRET_LEN}, key=len,
                        reverse=True):  # fmt: skip
        text = text.replace(value, REDACTED)
    return text


def parse_stream(lines: Iterable[str]) -> ParsedStream:
    """Linhas inválidas (ex.: aviso no stdout, linha truncada por timeout) são ignoradas."""
    out = ParsedStream()
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out.init = event
        elif kind == "result":
            out.result = event
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    out.tool_calls.append(
                        {"name": block.get("name", ""), "input": block.get("input") or {}}
                    )
    return out


# ---------- verified_state ----------
#
# Cada comando simples do shell vira "read" (lê o estado AWS pela API), "mutate" (muda o estado)
# ou nada (neutro). Neutros: inspeção de arquivo (grep, cat, ls, jq...), `terraform plan/show/
# state/output` (o plan não confere o efeito e o state é local) e o que não toca a AWS.

# AWS CLI: subcomandos que só leem; o resto das operações muda o estado.
_READ_PREFIXES = ("describe", "list", "get", "head", "scan", "query", "batch-get", "search",
                  "lookup", "wait")  # fmt: skip
_S3_READ = {"ls"}
_S3_NEUTRAL = {"presign"}
_AWS_VALUE_FLAGS = {"--region", "--profile", "--output", "--endpoint-url", "--query", "--color",
                    "--cli-read-timeout", "--cli-connect-timeout"}  # fmt: skip
_TF_MUTATE = {"apply", "destroy", "import"}
# boto3: só métodos com estes prefixos contam (evita `.get(`, `.copy(` de dict/list).
_BOTO3_READ = re.compile(r"\.((?:describe|list|get|head|batch_get|search|lookup)_\w+|scan|query)\(")
_BOTO3_MUTATE = re.compile(
    r"\.((?:create|delete|put|update|set|tag|untag|attach|detach|modify|enable|disable|authorize"
    r"|revoke|remove|add|copy|upload|terminate|start|stop|reboot|associate|disassociate|register"
    r"|deregister|send|restore|import|batch_write|change|replace|purge|apply|reset)_\w+"
    r"|run_instances|publish|subscribe|unsubscribe)\("
)
# Scripts do toolkit (own-tools): só leem a conta. verify/inventory contam em `verify_calls`.
TOOLKIT_READ_SCRIPTS = frozenset({"verify.py", "inventory.py", "diff_baseline.py", "whoami.py"})
TOOLKIT_VERIFY_SCRIPTS = frozenset({"verify.py", "inventory.py"})
# Prefixos que só repassam o comando seguinte.
_WRAPPERS = {"sudo", "env", "time", "nohup", "exec", "xargs", "command"}
_PUNCT = ";&|()<>`"
# `cmd <<'EOF' ... EOF`: o corpo vira um marcador no lugar do operador.
_HEREDOC = re.compile(
    r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL
)
_MARKER = "__HEREDOC_{}__"
_MARKER_RE = re.compile(r"__HEREDOC_(\d+)__")


@dataclass(frozen=True)
class StateOps:
    mutated: bool  # alguma chamada mudou o estado AWS
    verified: bool | None  # leitura pela API depois da última mudança; None sem mudança
    verify_calls: int  # chamadas a verify.py/inventory.py do toolkit


def _aws_op(tokens: list[str]) -> str | None:
    args: list[str] = []
    skip = False
    for tok in tokens:
        if skip:
            skip = False
        elif tok.startswith("--"):
            skip = tok in _AWS_VALUE_FLAGS
        else:
            args.append(tok)
        if len(args) == 2:
            break
    if len(args) < 2 or args[0] in ("configure", "help"):
        return None
    service, op = args
    if service == "s3":
        if op in _S3_NEUTRAL:
            return None
        return "read" if op in _S3_READ else "mutate"
    if service == "sts":
        return "read"
    return "read" if op.startswith(_READ_PREFIXES) else "mutate"


def _tf_op(tokens: list[str]) -> str | None:
    args = [t for t in tokens if not t.startswith("-")]
    return "mutate" if args and args[0] in _TF_MUTATE else None


def _python_op(code: str) -> str | None:
    """Script Python inteiro vira uma operação: muta se tem qualquer chamada boto3 que muda."""
    if _BOTO3_MUTATE.search(code):
        return "mutate"
    return "read" if _BOTO3_READ.search(code) else None


def _split_commands(text: str) -> list[list[str]]:
    """Comandos simples (listas de palavras) de um trecho de shell, na ordem."""
    text = text.replace("\\\n", " ").replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lexer.commenters = ""
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:  # aspas sem par: cai para a separação por espaço
        tokens = text.split()
    commands: list[list[str]] = [[]]
    redirect = False
    for tok in tokens:
        if redirect:
            commands[-1].append(f">{tok}")  # alvo de redirecionamento, marcado
            redirect = False
        elif tok and all(c in _PUNCT for c in tok):
            if ">" in tok or "<" in tok:
                redirect = True  # a próxima palavra é o arquivo (ou o fd, em `2>&1`)
            else:
                commands.append([])
        else:
            commands[-1].append(tok)
    return [c for c in commands if c]


class _Classifier:
    def __init__(self) -> None:
        self.scripts: dict[str, str] = {}  # nome do arquivo → conteúdo escrito pelo agente
        self.verify_calls = 0

    def write(self, path: str, content: str) -> None:
        if base := path.rsplit("/", 1)[-1]:
            self.scripts[base] = content

    def edit(self, path: str, old: str, new: str) -> None:
        base = path.rsplit("/", 1)[-1]
        if base in self.scripts:
            self.scripts[base] = self.scripts[base].replace(old, new)

    def shell(self, text: str, depth: int = 0) -> list[str]:
        bodies: list[str] = []

        def mark(m: re.Match[str]) -> str:
            bodies.append(m.group(4))
            return f" {_MARKER.format(len(bodies) - 1)} {m.group(3)}"

        text = _HEREDOC.sub(mark, text)
        ops: list[str] = []
        for words in _split_commands(text):
            ops += self._command(words, bodies, depth)
        return ops

    def _body(self, tok: str, bodies: list[str]) -> str | None:
        m = _MARKER_RE.fullmatch(tok)
        return bodies[int(m.group(1))] if m and int(m.group(1)) < len(bodies) else None

    def _command(self, words: list[str], bodies: list[str], depth: int) -> list[str]:
        targets = [w[1:] for w in words if w.startswith(">")]
        args = [w for w in words if not w.startswith(">")]
        while args and (re.match(r"^\w+=", args[0]) or args[0] in _WRAPPERS):
            args = args[1:]
        if not args:
            return []
        cmd, rest = args[0].rsplit("/", 1)[-1], args[1:]
        heredoc = next((b for t in rest if (b := self._body(t, bodies)) is not None), None)
        if cmd in ("cat", "tee") and heredoc is not None:
            for path in targets + [t for t in rest if cmd == "tee" and not t.startswith("-")
                                   and self._body(t, bodies) is None]:  # fmt: skip
                self.write(path, heredoc)
            return []
        if cmd == "aws":
            return [op] if (op := _aws_op(rest)) else []
        if cmd == "terraform":
            return [op] if (op := _tf_op(rest)) else []
        if re.fullmatch(r"python3?(\.\d+)?", cmd):
            return self._python(rest, heredoc)
        if cmd in ("bash", "sh", "zsh"):
            return self._sh(rest, heredoc, depth)
        if args[0].startswith("./"):
            return self._script(args[0], depth)
        return []

    def _python(self, rest: list[str], heredoc: str | None) -> list[str]:
        it = iter(rest)
        for tok in it:
            if tok == "-c":
                code = next(it, "")
                return [op] if (op := _python_op(code)) else []
            if tok == "-m":
                return []
            if tok in ("-W", "-X"):
                next(it, None)
            elif tok == "-" or _MARKER_RE.fullmatch(tok):
                break
            elif not tok.startswith("-"):
                return self._script(tok, 0)
        return [op] if heredoc is not None and (op := _python_op(heredoc)) else []

    def _sh(self, rest: list[str], heredoc: str | None, depth: int) -> list[str]:
        if depth >= 2:
            return []
        if "-c" in rest:
            i = rest.index("-c")
            return self.shell(rest[i + 1], depth + 1) if i + 1 < len(rest) else []
        script = next((t for t in rest if not t.startswith("-") and not _MARKER_RE.fullmatch(t)),
                      None)  # fmt: skip
        if script is not None:
            return self._script(script, depth)
        return self.shell(heredoc, depth + 1) if heredoc is not None else []

    def _script(self, path: str, depth: int) -> list[str]:
        base = path.rsplit("/", 1)[-1]
        toolkit = "/scripts/" in path or "CLAUDE_PLUGIN_ROOT" in path
        if base in TOOLKIT_READ_SCRIPTS and (toolkit or base not in self.scripts):
            self.verify_calls += base in TOOLKIT_VERIFY_SCRIPTS
            return ["read"]
        body = self.scripts.get(base)
        if body is None:
            return []
        if base.endswith(".py"):
            return [op] if (op := _python_op(body)) else []
        return self.shell(body, depth + 1) if depth < 2 else []


def _call_ops(clf: _Classifier, tool_calls: list[dict[str, Any]]) -> list[list[str]]:
    out: list[list[str]] = []
    for call in tool_calls:
        name, data = call.get("name"), call.get("input") or {}
        path = str(data.get("file_path", ""))
        ops: list[str] = []
        if name == "Write":
            clf.write(path, str(data.get("content", "")))
        elif name == "Edit":
            clf.edit(path, str(data.get("old_string", "")), str(data.get("new_string", "")))
        elif name == "Bash":
            ops = clf.shell(str(data.get("command", "")))
        out.append(ops)
    return out


def call_ops(tool_calls: list[dict[str, Any]]) -> list[list[str]]:
    """Operações ("read"/"mutate") de cada chamada, na ordem (lista vazia = neutra)."""
    return _call_ops(_Classifier(), tool_calls)


def state_ops(tool_calls: list[dict[str, Any]]) -> StateOps:
    """Classifica as chamadas de ferramenta de uma trajetória (ver o topo desta seção).

    Scripts escritos com Write/Edit (ou `cat > x <<EOF`) são classificados pelo conteúdo quando
    executados (`python3 x.py`, `bash x.sh`, `./x.sh`); um script Python com leitura e mutação
    conta como mutação. Os scripts do toolkit (`.../scripts/verify.py` etc.) contam como leitura.
    """
    clf = _Classifier()
    ops = [op for call in _call_ops(clf, tool_calls) for op in call]
    if "mutate" not in ops:
        return StateOps(False, None, clf.verify_calls)
    last = len(ops) - 1 - ops[::-1].index("mutate")
    return StateOps(True, "read" in ops[last + 1 :], clf.verify_calls)


def verified_state(tool_calls: list[dict[str, Any]]) -> bool | None:
    """Se uma leitura do estado AWS veio depois da última operação que o muda (None sem mudança)."""
    return state_ops(tool_calls).verified
