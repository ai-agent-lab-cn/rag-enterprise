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
        retrieval_report_passed=True,
        answer_report_passed=True,
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
