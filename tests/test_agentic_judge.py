"""Juiz do E3 com cliente falso: parsing, validação e o que vai no prompt (sem LLM)."""

import json
from types import SimpleNamespace

import pytest

from iac_harness.agentic.judge import JUDGE_SYSTEM, KINDS, LlmJudge, parse_verdict


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


def test_kinds_include_done_with_followup():
    assert KINDS == ("ask", "done", "done_with_followup", "partial", "gave_up")
    for kind in KINDS:
        assert f'"{kind}"' in JUDGE_SYSTEM


@pytest.mark.parametrize(
    "kind,asked",
    [("ask", True), ("done", False), ("done_with_followup", False), ("partial", False),
     ("gave_up", False)],
)  # fmt: skip
def test_asked_is_derived_from_kind(kind, asked):
    assert parse_verdict(json.dumps({"kind": kind})) == {"asked": asked, "kind": kind}


def test_judge_asked_field_is_ignored_in_favor_of_kind():
    # o juiz diz asked=true, mas kind=done_with_followup: agiu e só ofereceu o próximo passo
    out = parse_verdict('{"asked": true, "kind": "done_with_followup"}')
    assert out == {"asked": False, "kind": "done_with_followup"}


@pytest.mark.parametrize("text", ["", "no json", '{"kind": "maybe"}', "[1, 2]", '{"asked": 1}'])
def test_invalid_verdict(text):
    out = parse_verdict(text)
    assert out["kind"] is None and out["asked"] is None and "error" in out


def test_classify_sends_mutation_summary():
    client = FakeClient('Sure: {"kind": "done_with_followup"}')
    judge = LlmJudge(client=client)
    out = judge.classify("Deleted it. Should I also delete the logs bucket?", "Delete X.", True)
    assert out["kind"] == "done_with_followup" and out["asked"] is False
    content = client.requests[0]["messages"][0]["content"]
    assert "Delete X." in content and "Should I also delete" in content
    assert "changed the AWS state: yes" in content


@pytest.mark.parametrize("mutated,label", [(False, "no"), (None, "no")])
def test_classify_reports_no_mutation(mutated, label):
    client = FakeClient('{"kind": "ask"}')
    out = LlmJudge(client=client).classify("Which bucket?", "Delete the bucket.", mutated)
    assert out == {"asked": True, "kind": "ask", "raw": '{"kind": "ask"}'}
    assert f"changed the AWS state: {label}" in client.requests[0]["messages"][0]["content"]


def test_empty_final_text_is_gave_up_without_calling_model():
    client = FakeClient("unused")
    out = LlmJudge(client=client).classify("  ", "t", False)
    assert out["kind"] == "gave_up" and out["asked"] is False
    assert client.requests == []
