"""Agente de código que age sozinho no alvo. Hoje: Claude Code headless (`claude -p`) em container.

Outros agentes (Codex etc.) entram como outra classe com o mesmo `run`, sem mexer no runner.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from iac_harness.agentic.conditions import Condition
from iac_harness.agentic.stream import parse_stream, redact
from iac_harness.agentic.targets import AgentEnv, Target

DEFAULT_AGENT_IMAGE = "iac-harness-agent:latest"
AGENT_USER = "agent"  # não-root: como root, o bypassPermissions é recusado
TRANSCRIPT_FILENAME = "transcript.jsonl"
STDERR_FILENAME = "agent.stderr.txt"
# Onde o Claude Code guarda as sessões (`projects/<cwd>/<id>.jsonl`) no usuário do container.
SESSION_MOUNT = f"/home/{AGENT_USER}/.claude"
# Mesmo conjunto em toda condição: só o contexto muda. Sem AskUserQuestion (não há quem responda),
# sem web, sem subagentes.
AGENT_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,Skill"
# Credenciais do gateway: só pelo nome no argv, valor no env do cliente docker.
GATEWAY_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
SECRET_VARS = (*GATEWAY_VARS, "ANTHROPIC_API_KEY")
# Env mínimo do cliente docker (HOME: ~/.docker; DOCKER_*: host/contexto); nada mais do host.
_CLIENT_PASSTHROUGH = ("PATH", "HOME", "TMPDIR")


@dataclass
class AgentResult:
    final_text: str | None
    usage: dict[str, Any]
    cost_usd: float | None
    num_turns: int | None
    tool_calls: list[dict[str, Any]]
    tool_counts: dict[str, int]
    transcript: Path
    init: dict[str, Any] | None
    exit_code: int | None
    timed_out: bool
    duration_s: float
    is_error: bool | None = None
    subtype: str | None = None
    permission_denials: list[Any] = field(default_factory=list)
    condition_load_fail: bool = False  # system/init sem o plugin da condição: abortado
    session_id: str | None = None  # do system/init: permite retomar a sessão (--resume)


def turn_filename(base: str, turn: int) -> str:
    """Nome do arquivo do turno: o 1º é `base`; os seguintes levam `.turnN` antes da extensão."""
    if turn == 1:
        return base
    stem, dot, ext = base.partition(".")
    return f"{stem}.turn{turn}{dot}{ext}"


class CodingAgent(Protocol):
    """`run` de um turno. Com `session_dir`, a sessão é persistida nele (em vez de se perder com o
    container) e `resume_session` retoma a sessão anterior com `prompt` como a nova mensagem;
    só são passados quando há usuário simulado."""

    name: str

    def run(
        self, prompt: str, workdir: Path, condition: Condition, model: str, target: Target,
        out_dir: Path, session_dir: Path | None = None, resume_session: str | None = None,
        turn: int = 1,
    ) -> AgentResult: ...  # fmt: skip

    def version(self) -> dict[str, str | None]: ...


def secret_values(extra: dict[str, str] | None = None) -> list[str]:
    values = [os.environ.get(k) for k in SECRET_VARS]
    return [v for v in [*values, *(extra or {}).values()] if v]


def _event_kind(line: str) -> tuple[str, str | None] | None:
    """(type, subtype) de uma linha do stream-json; None se não for um evento JSON."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    return str(event.get("type")), event.get("subtype")


class ClaudeCodeAgent:
    name = "claude-code"

    def __init__(
        self,
        image: str = DEFAULT_AGENT_IMAGE,
        max_turns: int = 60,
        timeout_s: float = 1200,
    ) -> None:
        self.image = image
        self.max_turns = max_turns
        self.timeout_s = timeout_s

    def docker_argv(
        self, prompt: str, workdir: Path, condition: Condition, model: str, target: Target,
        name: str, env: AgentEnv | None = None, session_dir: Path | None = None,
        resume_session: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:  # fmt: skip
        """(argv do `docker run`, env do cliente docker com os valores secretos).

        `session_dir` (host) é montado em `SESSION_MOUNT` para a sessão sobreviver ao `--rm`;
        `resume_session` acrescenta `--resume <id>`. Os demais flags são os mesmos em todo turno.
        """
        env = env if env is not None else target.agent_env()
        missing = [k for k in GATEWAY_VARS if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"agente sem credencial do gateway: defina {', '.join(missing)}")
        secret_values = {
            **{k: os.environ[k] for k in GATEWAY_VARS},
            **env.secret,
        }
        argv = [
            "docker", "run", "--rm", "--name", name, "--user", AGENT_USER,
            *(a for net in target.agent_networks() for a in ("--network", net)),
            *(a for k, v in env.plain.items() for a in ("-e", f"{k}={v}")),
            *(a for k in secret_values for a in ("-e", k)),
            "-v", f"{Path(workdir).resolve()}:/work",
            *(a for host, cont in condition.mounts for a in ("-v", f"{host}:{cont}:ro")),
            *(("-v", f"{Path(session_dir).resolve()}:{SESSION_MOUNT}") if session_dir else ()),
            "-w", "/work",
            self.image,
            "claude", "-p", prompt,
            *(("--resume", resume_session) if resume_session else ()),
            "--model", model,
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions",
            "--permission-prompts", "none",
            "--max-turns", str(self.max_turns),
            "--tools", AGENT_TOOLS,
            "--strict-mcp-config",
            "--setting-sources", "",
            *condition.flags,
        ]  # fmt: skip
        base = {k: v for k, v in os.environ.items() if k in _CLIENT_PASSTHROUGH or
                k.startswith("DOCKER_")}  # fmt: skip
        return argv, {**base, **secret_values}

    def run(
        self, prompt: str, workdir: Path, condition: Condition, model: str, target: Target,
        out_dir: Path, session_dir: Path | None = None, resume_session: str | None = None,
        turn: int = 1,
    ) -> AgentResult:  # fmt: skip
        """Roda o container e grava o transcript (segredos filtrados, linha a linha) em out_dir.

        Turno > 1 (retomada): mesmo comando e flags, com `--resume`; o transcript e o stderr
        ganham o sufixo `.turnN` e o `system/init` é conferido de novo (plugin da condição).

        O stdout é lido em fluxo. O `system/init` pode vir depois de eventos de hook ou de
        instalação de plugin; se ele mostra que o plugin da condição não carregou, ou se uma
        mensagem do agente chega antes de um init válido, o container é removido na hora e
        `condition_load_fail` fica True (nada mais roda). Run que termina sem init também conta.
        """
        name = f"iac-agent-{uuid.uuid4().hex[:12]}"
        env = target.agent_env()
        argv, client_env = self.docker_argv(
            prompt, workdir, condition, model, target, name, env, session_dir, resume_session
        )
        secrets = secret_values(env.secret)
        needs_init = not condition.loaded(None)  # condição `none` não depende do init
        init_ok = False
        out_dir.mkdir(parents=True, exist_ok=True)
        transcript = out_dir / turn_filename(TRANSCRIPT_FILENAME, turn)
        stderr_path = out_dir / turn_filename(STDERR_FILENAME, turn)
        start = time.monotonic()
        timed_out = load_fail = False
        lines: list[str] = []
        with (
            stderr_path.open("w+", encoding="utf-8") as err_fh,
            transcript.open("w", encoding="utf-8") as out_fh,
        ):
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=err_fh, text=True, env=client_env
            )
            # Timeout de parede: um timer mata o container (matar só o cliente não o para).
            timer = threading.Timer(self.timeout_s, self._kill, (name,))
            timer.start()
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = redact(raw.rstrip("\n"), secrets)
                    lines.append(line)
                    out_fh.write(line + "\n")
                    out_fh.flush()
                    if not needs_init or init_ok:
                        continue
                    kind = _event_kind(line)
                    if kind == ("system", "init"):
                        init_ok = condition.loaded(parse_stream([line]).init)
                        load_fail = not init_ok
                    elif kind is not None and kind[0] in ("assistant", "user"):
                        load_fail = True  # o agente agiu antes de um init válido
                    if load_fail:
                        self._kill(name)
                        break
                code = proc.wait()
            finally:
                timed_out = not timer.is_alive() and not load_fail
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                    code = None
            err_fh.seek(0)
            stderr = err_fh.read()
        if needs_init and not init_ok and not timed_out:
            load_fail = True  # terminou sem system/init
        stderr_path.write_text(redact(stderr, secrets), encoding="utf-8")
        parsed = parse_stream(lines)
        return AgentResult(
            final_text=parsed.final_text,
            usage=parsed.usage,
            cost_usd=parsed.cost_usd,
            num_turns=parsed.num_turns,
            tool_calls=parsed.tool_calls,
            tool_counts=parsed.tool_counts,
            transcript=transcript,
            init=parsed.init,
            exit_code=None if (timed_out or load_fail) else code,
            timed_out=timed_out,
            duration_s=time.monotonic() - start,
            is_error=parsed.is_error,
            subtype=parsed.subtype,
            permission_denials=parsed.permission_denials,
            condition_load_fail=load_fail,
            session_id=(parsed.init or {}).get("session_id"),
        )

    @staticmethod
    def _kill(name: str) -> None:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    def version(self) -> dict[str, str | None]:
        """Versão do Claude Code e ID da imagem, para o run_config.json (tolerante a falha)."""
        out: dict[str, str | None] = {"agent_image": self.image}
        for key, args in (
            ("claude_code_version", ["docker", "run", "--rm", "--network", "none", self.image,
                                     "claude", "--version"]),
            ("agent_image_id", ["docker", "image", "inspect", "--format", "{{.Id}}", self.image]),
        ):  # fmt: skip
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
                out[key] = proc.stdout.strip() or f"erro: {proc.stderr.strip()[:200]}"
            except (subprocess.TimeoutExpired, OSError) as exc:
                out[key] = f"indisponível: {exc}"
        return out
