"""Camada de modelo de texto: fábrica, backend Anthropic e backend Codex (sem rede nem Codex)."""

import os
import subprocess
from pathlib import Path

import pytest

from iac_harness.agentic.judge import DEFAULT_JUDGE_MODEL
from iac_harness.llm import build_text_model
from iac_harness.llm import codex_cli as codex_mod
from iac_harness.llm.anthropic_text import DEFAULT_MODEL, AnthropicText
from iac_harness.llm.codex_cli import CodexText
from test_agentic_judge import FakeClient


def test_anthropic_complete_sends_system_and_user_and_returns_text():
    client = FakeClient("hello")
    model = AnthropicText("M", 77, client)
    assert model.complete("sys", "usr") == "hello"
    assert client.requests == [
        {"model": "M", "max_tokens": 77, "system": "sys",
         "messages": [{"role": "user", "content": "usr"}]}
    ]  # fmt: skip
    assert model.params == {"provider": "anthropic", "model": "M", "max_tokens": 77}


def test_default_models_match_the_old_judge_default():
    assert DEFAULT_MODEL == DEFAULT_JUDGE_MODEL == "Sonnet 5.5"


def test_factory_builds_each_provider(monkeypatch):
    monkeypatch.setattr(codex_mod.CodexText, "codex_version", "codex-cli 9.9")
    monkeypatch.setattr("iac_harness.llm.anthropic_text.Anthropic", lambda: FakeClient())
    a = build_text_model({"model": "X", "max_tokens": 5}, "anthropic")
    assert a.params == {"provider": "anthropic", "model": "X", "max_tokens": 5}
    c = build_text_model({}, "codex")
    assert c.params["provider"] == "codex" and c.params["model"] == "gpt-6-luna"
    assert c.params["codex_version"] == "codex-cli 9.9"
    c = build_text_model(
        {"provider": "codex", "model": "m", "reasoning_effort": "low"}, "anthropic"
    )
    assert (c.model, c.reasoning_effort) == ("m", "low")


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="provider desconhecido"):
        build_text_model({"provider": "gemini"}, "anthropic")


# ---------- Codex ----------


@pytest.fixture
def auth(tmp_path):
    path = tmp_path / "real-codex" / "auth.json"
    path.parent.mkdir()
    path.write_text("SECRET-TOKEN-ORIGINAL")
    return path


def _out_path(argv):
    return Path(argv[argv.index("--output-last-message") + 1])


def test_codex_argv_is_isolated_and_read_only(tmp_path):
    model = CodexText(reasoning_effort="low", auth_path=tmp_path / "a")
    argv = model.argv(tmp_path / "cwd", tmp_path / "out.txt")
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == "gpt-6-luna"
    assert argv[argv.index("--cd") + 1] == str(tmp_path / "cwd")
    assert _out_path(argv) == tmp_path / "out.txt"
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
        assert flag in argv
    for feature in ("hooks", "plugins", "memories", "shell_tool"):
        assert f"features.{feature}=false" in argv
    assert "skills.bundled.enabled=false" in argv
    assert 'model_reasoning_effort="low"' in argv
    assert argv[-1] == "-"  # o prompt vai pelo stdin


def test_codex_complete_uses_isolated_home_and_parses_output_file(tmp_path, auth, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gateway-secret")
    monkeypatch.setenv("HOME", str(tmp_path / "real-home"))
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, **kw)
        codex_home = Path(kw["env"]["CODEX_HOME"])
        seen["auth_is_link"] = (codex_home / "auth.json").is_symlink()
        seen["auth_target"] = (codex_home / "auth.json").resolve()
        seen["dirs"] = (codex_home, Path(kw["env"]["HOME"]), Path(kw["cwd"]))
        _out_path(argv).write_text("  the reply \n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(codex_mod.subprocess, "run", fake_run)
    out = CodexText(auth_path=auth).complete("SYSTEM", "USER")
    assert out == "the reply"
    assert seen["input"] == "SYSTEM\n\nUSER"
    assert "SYSTEM" not in " ".join(seen["argv"])
    env = seen["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in env and env["HOME"] != str(tmp_path / "real-home")
    assert seen["auth_is_link"] and seen["auth_target"] == auth.resolve()
    assert all(not d.exists() for d in seen["dirs"])  # diretórios temporários removidos
    assert auth.read_text() == "SECRET-TOKEN-ORIGINAL"


def test_codex_never_writes_the_real_login(tmp_path, auth, monkeypatch):
    def fake_run(argv, **kw):
        link = Path(kw["env"]["CODEX_HOME"]) / "auth.json"
        link.unlink()
        link.write_text("REFRESHED")  # o Codex trocou o arquivo (renovação do token)
        _out_path(argv).write_text("x")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(codex_mod.subprocess, "run", fake_run)
    CodexText(auth_path=auth).complete("s", "u")
    assert auth.read_text() == "SECRET-TOKEN-ORIGINAL"  # a renovação se perde (limitação)
    assert not hasattr(CodexText, "_keep_refreshed_login")


def test_codex_failure_raises_without_leaking_prompt(tmp_path, auth, monkeypatch):
    monkeypatch.setattr(
        codex_mod.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"),
    )  # fmt: skip
    with pytest.raises(RuntimeError, match="boom"):
        CodexText(auth_path=auth).complete("s", "u")


def test_codex_empty_output_raises(tmp_path, auth, monkeypatch):
    monkeypatch.setattr(
        codex_mod.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
    )
    with pytest.raises(RuntimeError, match="falhou"):
        CodexText(auth_path=auth).complete("s", "u")


def test_codex_without_login_raises(tmp_path):
    with pytest.raises(RuntimeError, match="sem login"):
        CodexText(auth_path=tmp_path / "missing.json").complete("s", "u")


def test_codex_version_is_recorded_and_tolerates_failure(monkeypatch):
    monkeypatch.setattr(
        codex_mod.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "codex-cli 1.2.3\n", ""),
    )  # fmt: skip
    assert CodexText(auth_path=Path("x")).params["codex_version"] == "codex-cli 1.2.3"

    def boom(argv, **kw):
        raise OSError("no codex")

    monkeypatch.setattr(codex_mod.subprocess, "run", boom)
    assert CodexText(auth_path=Path("x")).codex_version.startswith("indisponível")


def test_real_auth_path_follows_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_mod.real_auth_path() == tmp_path / "auth.json"
    monkeypatch.delenv("CODEX_HOME")
    assert codex_mod.real_auth_path() == Path(os.path.expanduser("~")) / ".codex" / "auth.json"
