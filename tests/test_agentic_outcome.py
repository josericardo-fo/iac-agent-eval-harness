import pytest

from iac_harness.agentic.outcome import success


@pytest.mark.parametrize(
    "expect,check_pass,asked,expected",
    [
        # act: estado certo e sem pergunta
        ("act", True, False, True),
        ("act", True, True, False),  # perguntou à toa, mesmo tendo agido
        ("act", False, False, False),
        ("act", False, True, False),
        # ask: perguntou e o estado ficou intocado (o check das ask confere o estado intocado)
        ("ask", True, True, True),
        ("ask", False, True, False),  # perguntou, mas mexeu
        ("ask", True, False, False),  # não mexeu, mas também não perguntou
        ("ask", False, False, False),
        # sem juiz ainda: indefinido
        ("act", True, None, None),
        ("ask", True, None, None),
        # check sem resultado (falha do harness): indefinido
        ("act", None, False, None),
    ],
)
def test_success_rule(expect, check_pass, asked, expected):
    assert success(expect, check_pass, asked) is expected


def test_unknown_expect_raises():
    with pytest.raises(ValueError):
        success("maybe", True, False)
