"""Segurança do container do agente (E3), no molde de test_safety.py: só o argv, sem Docker."""

import re
from pathlib import Path

import pytest

from iac_harness.agentic.agents import ClaudeCodeAgent
from iac_harness.agentic.conditions import resolve_condition
from iac_harness.agentic.targets import AwsTarget, FlociTarget
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import DEFAULT_ALLOWED_HOSTS, Toolchain

SECRETS = {
    "ANTHROPIC_AUTH_TOKEN": "sk-gateway-SECRET-0001",
    "ANTHROPIC_BASE_URL": "https://gateway.example.org/v1",
    "IAC_AWS_ACCESS_KEY_ID": "AKIAREALKEY0001",
    "IAC_AWS_SECRET_ACCESS_KEY": "real-aws-secret-0001",
    "AWS_PROFILE": "prod",
    "AWS_SESSION_TOKEN": "host-session-token-0001",
}


@pytest.fixture
def host_env(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)


def _plugin_root(tmp_path: Path) -> Path:
    """Árvore falsa com os diretórios das condições (o toolkit real pode não existir)."""
    for rel in (
        "toolkit/plugins/own-rules",
        "toolkit/plugins/own-tools",
        "toolkit/plugins/own-rules-v2",
        "third_party/agent-toolkit-for-aws/plugins/aws-core",
        "third_party/agent-toolkit-for-aws/rules",
    ):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / "third_party/agent-toolkit-for-aws/rules/aws-agent-rules.md").write_text("r")
    for plugin in ("own-rules", "own-tools", "own-rules-v2"):
        (tmp_path / f"toolkit/plugins/{plugin}/RULES.md").write_text("r")
    return tmp_path


def _floci_target() -> FlociTarget:
    tc = Toolchain(mode="docker")
    return FlociTarget(tc, FlociExecutor(tc))


def _aws_target() -> AwsTarget:
    tc = Toolchain(mode="docker", target="aws")
    return AwsTarget(tc, RealAwsExecutor(tc, expected_arn="arn:aws:iam::1:user/iac-harness"))


def _argv(target, tmp_path, condition="none"):
    agent = ClaudeCodeAgent(max_turns=60, timeout_s=1200)
    cond = resolve_condition(condition, _plugin_root(tmp_path))
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    argv, client_env = agent.docker_argv("do it", work, cond, "Haiku 5.5", target, "c1")
    return argv, client_env, work


def _flag_values(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


@pytest.mark.parametrize("make_target", [_floci_target, _aws_target])
def test_argv_has_no_secret_values(host_env, tmp_path, make_target):
    argv, client_env, _ = _argv(make_target(), tmp_path, "own-tools")
    joined = " ".join(argv)
    for value in SECRETS.values():
        assert value not in joined
    # segredos só pelo nome; o valor vai no env do cliente docker
    assert "ANTHROPIC_AUTH_TOKEN" in _flag_values(argv, "-e")
    assert "ANTHROPIC_BASE_URL" in _flag_values(argv, "-e")
    assert client_env["ANTHROPIC_AUTH_TOKEN"] == SECRETS["ANTHROPIC_AUTH_TOKEN"]
    assert "AWS_PROFILE" not in joined and "AWS_SESSION_TOKEN" not in joined


def test_floci_target_never_gets_iac_aws(host_env, tmp_path):
    argv, client_env, _ = _argv(_floci_target(), tmp_path)
    assert "IAC_AWS" not in " ".join(argv)
    envs = _flag_values(argv, "-e")
    assert "AWS_ACCESS_KEY_ID=test" in envs and "AWS_SECRET_ACCESS_KEY=test" in envs
    assert "AWS_ENDPOINT_URL=http://floci:4566" in envs
    assert SECRETS["IAC_AWS_SECRET_ACCESS_KEY"] not in client_env.values()
    assert "AWS_ACCESS_KEY_ID" not in client_env


def test_aws_target_maps_iac_aws_by_name(host_env, tmp_path):
    argv, client_env, _ = _argv(_aws_target(), tmp_path)
    envs = _flag_values(argv, "-e")
    assert "AWS_ACCESS_KEY_ID" in envs and "AWS_SECRET_ACCESS_KEY" in envs
    assert client_env["AWS_ACCESS_KEY_ID"] == SECRETS["IAC_AWS_ACCESS_KEY_ID"]
    assert not any(e.startswith("AWS_ENDPOINT_URL") for e in envs)


@pytest.mark.parametrize("make_target", [_floci_target, _aws_target])
def test_runs_as_non_root_agent_user(host_env, tmp_path, make_target):
    argv, _, _ = _argv(make_target(), tmp_path)
    assert _flag_values(argv, "--user") == ["agent"]


def test_floci_networks_are_exactly_sandbox_and_egress(host_env, tmp_path):
    argv, _, _ = _argv(_floci_target(), tmp_path)
    assert sorted(_flag_values(argv, "--network")) == ["iac-egress", "iac-sandbox"]
    envs = _flag_values(argv, "-e")
    assert "HTTPS_PROXY=http://egress-proxy:3128" in envs
    assert any(e.startswith("NO_PROXY=") and "floci" in e for e in envs)


def test_aws_networks_are_only_egress(host_env, tmp_path):
    argv, _, _ = _argv(_aws_target(), tmp_path)
    assert _flag_values(argv, "--network") == ["iac-egress-aws"]
    assert "HTTPS_PROXY=http://egress-proxy-aws:3128" in _flag_values(argv, "-e")


CONDITIONS = ["none", "aws-core", "own-skill-only", "own-rules", "own-tools", "own-rules-v2"]


@pytest.mark.parametrize("condition", CONDITIONS)
def test_mounts_are_only_work_rw_and_condition_dirs_ro(host_env, tmp_path, condition):
    argv, _, work = _argv(_floci_target(), tmp_path, condition)
    mounts = _flag_values(argv, "-v")
    assert f"{work.resolve()}:/work" in mounts
    others = [m for m in mounts if m != f"{work.resolve()}:/work"]
    assert all(m.endswith(":ro") for m in others)
    assert all(Path(m.split(":")[0]).is_relative_to(tmp_path) for m in others)
    assert len(others) == {
        "none": 0, "aws-core": 2, "own-skill-only": 1, "own-rules": 2, "own-tools": 2,
        "own-rules-v2": 2,
    }[condition]
    joined = " ".join(argv)
    assert ".claude" not in joined and ".aws" not in joined
    assert str(Path.home()) not in " ".join(mounts)
    # HOME é o do container (/home/agent), nunca o do host
    assert not any(e.startswith("HOME=") for e in _flag_values(argv, "-e"))


def test_condition_flags(host_env, tmp_path):
    argv, _, _ = _argv(_floci_target(), tmp_path, "aws-core")
    assert _flag_values(argv, "--plugin-dir") == ["/opt/conditions/aws-core"]
    assert _flag_values(argv, "--append-system-prompt-file") == [
        "/opt/conditions/aws-agent-rules.md"
    ]
    argv, _, _ = _argv(_floci_target(), tmp_path, "own-rules")
    assert _flag_values(argv, "--plugin-dir") == ["/opt/conditions/own-rules"]
    assert _flag_values(argv, "--append-system-prompt-file") == ["/opt/conditions/RULES.md"]
    argv, _, _ = _argv(_floci_target(), tmp_path, "own-skill-only")
    assert _flag_values(argv, "--plugin-dir") == ["/opt/conditions/own-rules"]
    assert "--append-system-prompt-file" not in argv
    argv, _, _ = _argv(_floci_target(), tmp_path, "none")
    assert "--plugin-dir" not in argv and "--append-system-prompt-file" not in argv
    assert _flag_values(argv, "--max-turns") == ["60"]
    assert _flag_values(argv, "--permission-mode") == ["bypassPermissions"]
    assert _flag_values(argv, "--permission-prompts") == ["none"]
    # mesmo conjunto fixo de ferramentas em toda condição; sem MCP (nem o do aws-core)
    assert _flag_values(argv, "--tools") == ["Bash,Read,Write,Edit,Glob,Grep,Skill"]
    assert "--strict-mcp-config" in argv
    assert _flag_values(argv, "--setting-sources") == [""]


def test_missing_condition_dir_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_condition("own-tools", tmp_path)


def test_unknown_condition_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_condition("everything", tmp_path)


def test_condition_loaded_checks_init_plugins(tmp_path):
    cond = resolve_condition("own-tools", _plugin_root(tmp_path))
    ok = {
        "plugins": [{"name": "own-tools", "path": "/opt/conditions/own-tools"}],
        "skills": ["deep-research", "own-tools:iac-cloud"],
    }
    assert cond.loaded(ok) is True
    assert cond.loaded({"plugins": [{"name": "cc-plugin-agents-md"}], "skills": []}) is False
    # plugin carregado, mas sem nenhuma skill dele
    assert cond.loaded({"plugins": ok["plugins"], "skills": ["deep-research"]}) is False
    assert cond.loaded(None) is False
    assert resolve_condition("none", tmp_path).loaded({"plugins": []}) is True


@pytest.mark.parametrize("make_target", [_floci_target, _aws_target])
def test_target_cleanup_does_not_mask_original_error(host_env, make_target, monkeypatch, capsys):
    target = make_target()

    def broken():
        raise RuntimeError("cleanup quebrou")

    monkeypatch.setattr(target.executor, "cleanup", broken)
    with pytest.raises(ValueError, match="original"):
        try:
            raise ValueError("original")
        finally:
            target.cleanup()
    assert "cleanup quebrou" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="cleanup quebrou"):
        target.cleanup()


def _compose_services() -> dict[str, str]:
    """Nome do serviço → bloco de texto dele no compose (sem dependência de YAML)."""
    text = (Path(__file__).parent.parent / "docker" / "compose.yaml").read_text()
    body = text.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    parts = re.split(r"^  (\w[\w-]*):.*$", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_floci_workers_match_toolchain_allowlist_and_compose():
    from iac_harness.agentic.targets import FLOCI_WORKERS

    services = _compose_services()
    assert [w.host for w in FLOCI_WORKERS] == ["floci", "floci2", "floci3", "floci4"]
    for w in FLOCI_WORKERS:
        assert w.host in DEFAULT_ALLOWED_HOSTS
        assert w.host in services, w.host
        assert f'"127.0.0.1:{w.host_port}:4566"' in services[w.host]
        # compose: <projeto>-<serviço>-1
        assert w.container == f"iac-harness-{w.host}-1"
        assert w.endpoint == f"http://{w.host}:4566"


def test_worker_cfg_comes_from_floci_workers():
    from iac_harness.agentic.targets import FLOCI_WORKERS
    from iac_harness.cli import _worker_cfg

    cfg = {"sandbox": {"endpoint": "http://floci:4566", "region": "us-east-1"}, "x": 1}
    assert _worker_cfg(cfg, 0) is cfg
    w = _worker_cfg(cfg, 2)
    assert w["sandbox"] == {
        "endpoint": FLOCI_WORKERS[2].endpoint,
        "host_endpoint": f"http://127.0.0.1:{FLOCI_WORKERS[2].host_port}",
        "container": FLOCI_WORKERS[2].container,
        "region": "us-east-1",
    }
