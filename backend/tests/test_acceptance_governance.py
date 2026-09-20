from backend.app.acceptance_governance import AcceptanceSnapshot, evaluate_acceptance


def complete_snapshot() -> AcceptanceSnapshot:
    return AcceptanceSnapshot(
        external_source_count=1,
        successful_sync_runs=2,
        incremental_change_count=3,
        deleted_count=1,
        acl_change_count=1,
        parsed_version_count=2,
        active_index_count=1,
        active_index_version_id="iv_active",
        retrieval_report_passed=True,
        retrieval_report_id="retrieval-official",
        answer_report_passed=True,
        answer_report_id="answer-official",
        acl_leak_count=0,
        citation_failure_count=0,
        regression_failed_count=0,
    )


def test_complete_enterprise_rag_chain_passes_all_eight_steps() -> None:
    result = evaluate_acceptance(complete_snapshot())

    assert result.status == "passed"
    assert [step.step_key for step in result.steps] == [
        "runtime",
        "external_source",
        "incremental_sync",
        "parse_and_index",
        "retrieval_and_acl",
        "trusted_answer",
        "evaluation_and_regression",
        "acceptance_report",
    ]
    assert all(step.status == "passed" for step in result.steps)


def test_missing_real_source_and_incremental_evidence_blocks_acceptance() -> None:
    snapshot = complete_snapshot().model_copy(update={"external_source_count": 0, "successful_sync_runs": 0})

    result = evaluate_acceptance(snapshot)

    assert result.status == "blocked"
    blocked = {step.step_key for step in result.steps if step.status == "blocked"}
    assert {"external_source", "incremental_sync"} <= blocked


def test_acl_or_citation_security_failure_fails_acceptance() -> None:
    snapshot = complete_snapshot().model_copy(update={"acl_leak_count": 1, "citation_failure_count": 1})

    result = evaluate_acceptance(snapshot)

    assert result.status == "failed"
    failed = {step.step_key for step in result.steps if step.status == "failed"}
    assert {"retrieval_and_acl", "trusted_answer"} <= failed


def test_acceptance_steps_bind_their_release_evidence_ids() -> None:
    result = evaluate_acceptance(complete_snapshot())
    steps = {step.step_key: step for step in result.steps}

    assert steps["parse_and_index"].evidence["active_index_version_id"] == "iv_active"
    assert steps["retrieval_and_acl"].evidence["retrieval_report_id"] == "retrieval-official"
    assert steps["trusted_answer"].evidence["answer_report_id"] == "answer-official"
