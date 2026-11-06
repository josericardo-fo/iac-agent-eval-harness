from iac_harness.agentic.metrics import compute_agent_metrics, merge_judgments


def _final(task, cond, rep, check, asked=False, expect="act", model="Haiku 5.5", **kw):
    return {
        "event": "final", "trajectory_id": f"{task}-{cond}-{model}-{rep}", "task_id": task,
        "category": "modify", "expect": expect, "condition": cond, "model": model, "rep": rep,
        "target": "floci", "termination": "DONE", "check_pass": check, "asked": None,
        "success": None, "collateral_ok": None, "verified_state": True, "cost_usd": 0.01,
        "num_turns": 4, "n_tool_calls": 3, "tool_counts": {"Bash": 3},
        "input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000, "total_tokens": 1115, "duration_s": 30.0,
        "strata_intent": "ambiguous" if expect == "ask" else "clear", "strata_origin": "authored",
        "strata_services": ["SQS", "S3"], "strata_difficulty": None, "strata_category": "modify",
        "strata_target": "both", "_asked": asked, **kw,
    }  # fmt: skip


def _judge(events):
    rows = [{"trajectory_id": e["trajectory_id"], "asked": e["_asked"], "kind": "done"}
            for e in events]  # fmt: skip
    return merge_judgments(events, rows)


def test_merge_judgments_sets_asked_and_success():
    events = [
        _final("t1", "none", 1, True),
        _final("t2", "none", 1, True, asked=True, expect="ask"),
        _final("t3", "none", 1, True, asked=True),
    ]
    merged = _judge(events)
    assert [e["success"] for e in merged] == [True, True, False]
    assert merged[2]["asked"] is True and merged[2]["judge_kind"] == "done"


def test_unjudged_rows_are_counted_and_left_out_of_success():
    events = [_final("t1", "none", 1, True), _final("t2", "none", 1, True)]
    merged = merge_judgments(events, [{"trajectory_id": "t1-none-Haiku 5.5-1", "asked": False,
                                       "kind": "done"}])  # fmt: skip
    m = compute_agent_metrics(merged)
    block = m["by_condition_model"]["none|Haiku 5.5"]
    assert m["n_unjudged"] == 1
    assert block["success"]["n"] == 1 and block["state_pass"]["n"] == 2


def test_harness_failures_are_excluded():
    events = [_final("t1", "none", 1, None, termination="CONDITION_LOAD_FAIL"),
              _final("t2", "none", 1, True)]  # fmt: skip
    m = compute_agent_metrics(_judge(events))
    assert m["n_trajectories"] == 1 and m["harness_failures"] == {"CONDITION_LOAD_FAIL": 1}


def test_paired_comparison_against_none():
    events = []
    for task in ("t1", "t2", "t3"):
        for rep in (1, 2, 3):
            events.append(_final(task, "none", rep, check=False))
            events.append(_final(task, "own-tools", rep, check=True))
    m = compute_agent_metrics(_judge(events), n_boot=200)
    cmp = m["vs_none"]["Haiku 5.5"]["success"][0]
    assert cmp["pair"] == "nonexown-tools"
    assert cmp["n_pairs"] == 3 and cmp["only_first"] == 0 and cmp["only_second"] == 3
    assert cmp["diff_mean"] == 1.0


def test_strata_axes_split_lists_and_target():
    events = [_final("t1", "none", 1, True), _final("t2", "none", 1, False)]
    m = compute_agent_metrics(_judge(events), n_boot=50)
    by_service = m["by_strata"]["services"]
    assert set(by_service) == {"SQS", "S3"}
    assert by_service["SQS"]["none|Haiku 5.5"]["state_pass"]["n"] == 2
    assert "floci" in m["by_target"]
    block = m["by_condition_model"]["none|Haiku 5.5"]
    assert block["tokens"]["cache_read_input"] == 2000
    assert block["cost_usd"] == 0.02
    assert block["tool_calls"] == {"Bash": 6}
    assert block["verified_state"]["k"] == 2


def _grid(spec, model="Haiku 5.5"):
    """spec: {(task, cond): [check por rep]} → eventos julgados (asked=False)."""
    events = []
    for (task, cond), checks in spec.items():
        for rep, check in enumerate(checks, 1):
            tokens = 100 if cond == "none" else 300
            events.append(_final(task, cond, rep, check, model=model, total_tokens=tokens))
    return _judge(events)


def test_per_task_table_against_none():
    rows = _grid(
        {
            ("t1", "none"): [True, True, False],
            ("t1", "own-tools"): [False, False, True],
            ("t2", "none"): [False, False, False],
            ("t2", "own-tools"): [True, True, True],
        }
    )
    table = compute_agent_metrics(rows, n_boot=50)["per_task"]["Haiku 5.5"]["own-tools"]
    assert table == [
        {"task_id": "t1", "none_success": True, "cond_success": False, "none_tokens": 100.0,
         "cond_tokens": 300.0, "tokens_ratio": 3.0},
        {"task_id": "t2", "none_success": False, "cond_success": True, "none_tokens": 100.0,
         "cond_tokens": 300.0, "tokens_ratio": 3.0},
    ]  # fmt: skip


def test_easy_task_harm():
    rows = _grid({
        ("t1", "none"): [True, True, True], ("t1", "own-tools"): [False, False, True],  # piora
        ("t2", "none"): [True, True, True], ("t2", "own-tools"): [True, True, False],
        ("t3", "none"): [True, True, False], ("t3", "own-tools"): [False, False, False],  # difícil
    })  # fmt: skip
    harm = compute_agent_metrics(rows, n_boot=50)["easy_harm"]["Haiku 5.5"]["own-tools"]
    assert harm["n_easy"] == 2
    assert harm["harmed"]["k"] == 1 and harm["harmed"]["n"] == 2
    assert harm["harmed_tasks"] == ["t1"]


def test_compare_runs_agreement_and_discordant():
    from iac_harness.agentic.metrics import compare_runs

    floci = _grid({("t1", "none"): [True, True, False], ("t2", "none"): [True, True, True],
                   ("t3", "none"): [False, False, False]})  # fmt: skip
    aws = _grid({("t1", "none"): [True, True, True], ("t2", "none"): [False, False, True],
                 ("t9", "none"): [True, True, True]})  # fmt: skip
    out = compare_runs(floci, aws)
    assert out["n_pairs"] == 2
    assert out["agreement"]["k"] == 1 and out["agreement"]["n"] == 2
    assert out["discordant"] == [
        {"task_id": "t2", "condition": "none", "model": "Haiku 5.5", "first": True,
         "second": False}
    ]  # fmt: skip
    assert out["only_first"] == 1 and out["only_second"] == 1


def test_judge_sample_is_reproducible_and_skips_harness_failures():
    from iac_harness.agentic.metrics import judge_sample

    events = [_final(f"t{i}", "none", 1, True, final_text=f"msg {i} " + "x" * 600)
              for i in range(10)]  # fmt: skip
    events.append(_final("bad", "none", 1, None, termination="CONDITION_LOAD_FAIL"))
    judgments = [{"trajectory_id": e["trajectory_id"], "kind": "done", "asked": False}
                 for e in events]  # fmt: skip
    a = judge_sample(events, judgments, 4, seed=7)
    assert a == judge_sample(events, judgments, 4, seed=7)
    assert len(a) == 4 and len({r["trajectory_id"] for r in a}) == 4
    assert all(r["judge_kind"] == "done" and r["human_kind"] == "" for r in a)
    assert all(len(r["final_text_excerpt"]) <= 500 for r in a)
    assert "bad" not in {r["trajectory_id"].split("-")[0] for r in a}
    assert len(judge_sample(events, judgments, 30, seed=7)) == 10


def test_judge_agreement_ignores_unlabeled_rows():
    from iac_harness.agentic.metrics import judge_agreement

    rows = [
        {"trajectory_id": "a", "judge_kind": "ask", "human_kind": "ask"},
        {"trajectory_id": "b", "judge_kind": "done", "human_kind": "done_with_followup"},
        {"trajectory_id": "c", "judge_kind": "done", "human_kind": " done "},
        {"trajectory_id": "d", "judge_kind": "ask", "human_kind": ""},
    ]
    out = judge_agreement(rows)
    assert out["kind"]["k"] == 2 and out["kind"]["n"] == 3
    # asked (kind == ask) concorda nos três rotulados
    assert out["asked"]["k"] == 3 and out["asked"]["n"] == 3
    assert out["unlabeled"] == 1
    assert out["disagreements"] == [
        {"trajectory_id": "b", "judge_kind": "done", "human_kind": "done_with_followup"}
    ]


def test_judge_agreement_rejects_unknown_human_kind():
    import pytest

    from iac_harness.agentic.metrics import judge_agreement

    with pytest.raises(ValueError, match="human_kind"):
        judge_agreement([{"trajectory_id": "a", "judge_kind": "ask", "human_kind": "maybe"}])
