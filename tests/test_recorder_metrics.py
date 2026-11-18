import pytest

from iac_harness.metrics import (
    holm,
    load_events,
    mcnemar_exact,
    paired_bootstrap,
    wilson,
)
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.types import Artifact


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "sub" / "events.jsonl"
    rec = JsonlRecorder(path)
    rec.record({"event": "x", "v": 1, "s": "ação"})
    # flush imediato: legível antes de fechar
    assert load_events(path) == [{"event": "x", "s": "ação", "v": 1}]
    rec.record({"event": "y"})
    rec.close()
    assert len(load_events(path)) == 2


def test_wilson_known_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) == (0.0, 0.0)


def test_mcnemar_exact_small_table():
    assert mcnemar_exact(1, 5) == pytest.approx(0.21875)
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0


def test_holm():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_bootstrap_deterministic_and_paired():
    a = {"t1": 0.0, "t2": 0.0, "t3": 0.0}
    b = {"t1": 1.0, "t2": 1.0, "t3": 1.0}
    assert paired_bootstrap(a, b, 200, 1) == (1.0, 1.0, 1.0)
    x = {"t1": 0.0, "t2": 1.0, "t3": 0.0, "t4": 1.0}
    y = {"t1": 1.0, "t2": 1.0, "t3": 1.0, "t4": 0.0}
    assert paired_bootstrap(x, y, 200, 7) == paired_bootstrap(x, y, 200, 7)


def test_artifact_hash_is_order_independent():
    a = Artifact({"a.tf": "1", "b.tf": "2"})
    b = Artifact({"b.tf": "2", "a.tf": "1"})
    assert a.sha256 == b.sha256
    assert a.sha256 != Artifact({"a.tf": "12", "b.tf": ""}).sha256
