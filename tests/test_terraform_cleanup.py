"""remove_terraform_dir: rmtree primeiro, fallback via container, nunca levanta."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from iac_harness.toolchain import Toolchain, remove_terraform_dir

_REAL_RMTREE = shutil.rmtree


class _FakeToolchain:
    """Toolchain falso: registra chamadas; `really_remove` apaga de fato o diretório."""

    def __init__(self, mode: str = "docker", really_remove: bool = True, raises: bool = False):
        self.mode = mode
        self.really_remove = really_remove
        self.raises = raises
        self.calls: list[tuple[list[str], Path]] = []

    def run(self, args, workdir, timeout_s=None):
        self.calls.append((list(args), Path(workdir)))
        if self.raises:
            raise OSError("docker indisponível")
        if self.really_remove:
            _REAL_RMTREE(Path(workdir) / ".terraform")
        return subprocess.CompletedProcess(args, 0, "", "")


def _make_tf_dir(path: Path) -> None:
    (path / ".terraform" / "providers").mkdir(parents=True)
    (path / ".terraform" / "providers" / "p").write_text("x")
    (path / ".terraform.lock.hcl").write_text("lock")
    (path / "terraform.tfstate").write_text("{}")


def _stub_rmtree_noop(monkeypatch):
    """Simula arquivos de outro usuário: o rmtree do host não consegue apagar."""
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)


def test_normal_path_does_not_call_toolchain(tmp_path):
    _make_tf_dir(tmp_path)
    tc = _FakeToolchain()
    remove_terraform_dir(tmp_path, tc)
    assert not (tmp_path / ".terraform").exists()
    assert tc.calls == []
    assert (tmp_path / ".terraform.lock.hcl").exists()
    assert (tmp_path / "terraform.tfstate").exists()


def test_fallback_runs_rm_in_container_with_right_workdir(tmp_path, monkeypatch):
    _make_tf_dir(tmp_path)
    _stub_rmtree_noop(monkeypatch)
    tc = _FakeToolchain()
    remove_terraform_dir(tmp_path, tc)
    assert tc.calls == [(["rm", "-rf", "/work/.terraform"], tmp_path)]
    assert not (tmp_path / ".terraform").exists()
    assert (tmp_path / ".terraform.lock.hcl").exists()


def test_local_mode_never_calls_toolchain(tmp_path, monkeypatch, capsys):
    _make_tf_dir(tmp_path)
    _stub_rmtree_noop(monkeypatch)
    tc = _FakeToolchain(mode="local")
    remove_terraform_dir(tmp_path, tc)
    assert tc.calls == []
    assert "warning" in capsys.readouterr().err


def test_no_toolchain_only_tries_rmtree(tmp_path, monkeypatch, capsys):
    _make_tf_dir(tmp_path)
    _stub_rmtree_noop(monkeypatch)
    remove_terraform_dir(tmp_path, None)
    assert "warning" in capsys.readouterr().err


def test_still_present_warns_on_stderr_and_does_not_raise(tmp_path, monkeypatch, capsys):
    _make_tf_dir(tmp_path)
    _stub_rmtree_noop(monkeypatch)
    tc = _FakeToolchain(really_remove=False)
    remove_terraform_dir(tmp_path, tc)
    err = capsys.readouterr().err
    assert "warning" in err and str(tmp_path / ".terraform") in err
    assert (tmp_path / ".terraform").exists()


def test_toolchain_failure_does_not_raise(tmp_path, monkeypatch, capsys):
    _make_tf_dir(tmp_path)
    _stub_rmtree_noop(monkeypatch)
    remove_terraform_dir(tmp_path, _FakeToolchain(raises=True))
    assert "warning" in capsys.readouterr().err


def test_timeout_does_not_raise(tmp_path, monkeypatch, capsys):
    _make_tf_dir(tmp_path)
    _stub_rmtree_noop(monkeypatch)

    class _Slow(_FakeToolchain):
        def run(self, args, workdir, timeout_s=None):
            raise subprocess.TimeoutExpired(args, 1)

    remove_terraform_dir(tmp_path, _Slow())
    assert "warning" in capsys.readouterr().err


def test_missing_dir_is_a_noop(tmp_path):
    tc = _FakeToolchain()
    remove_terraform_dir(tmp_path, tc)
    assert tc.calls == []


def test_real_toolchain_type_is_accepted(tmp_path):
    # sanidade de tipo: o helper aceita um Toolchain local de verdade
    remove_terraform_dir(tmp_path, Toolchain(mode="local"))


def test_run_gates_uses_toolchain_fallback(tmp_path, monkeypatch):
    from iac_harness.runner import run_gates
    from iac_harness.types import CheckResult, CheckStatus, Stage, TaskOracle
    from iac_harness.workspace import Workspace

    class _Gate:
        def check(self, workspace, oracle):
            _make_tf_dir(workspace.path)
            return CheckResult(Stage.VALIDATE, CheckStatus.PASS)

    _stub_rmtree_noop(monkeypatch)
    tc = _FakeToolchain()
    run_gates(
        [Stage.VALIDATE],
        {Stage.VALIDATE: _Gate()},
        Workspace(tmp_path, "h"),
        TaskOracle({}, ""),
        toolchain=tc,
    )
    assert tc.calls == [(["rm", "-rf", "/work/.terraform"], tmp_path)]
