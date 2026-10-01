import pytest

from iac_harness.cli import _load_prevalidation


def test_run_without_prevalidation_fails_unless_explicit(tmp_path):
    with pytest.raises(SystemExit):
        _load_prevalidation(None, allow_missing=False)
    assert _load_prevalidation(None, allow_missing=True) == {}
