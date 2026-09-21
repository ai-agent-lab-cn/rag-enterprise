from backend.app.acceptance_governance import AcceptanceSnapshot, evaluate_acceptance


def complete_snapshot() -> AcceptanceSnapshot:
    return AcceptanceSnapshot(
        runtime_ready=True,
        schema_version=41,
        required_schema_version=41,
        commit_sha="abcdef1234567890",
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
        regression_case_count=2,
        regression_unverified_count=0,
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


def test_runtime_without_traceable_commit_or_current_schema_is_blocked() -> None:
    snapshot = complete_snapshot().model_copy(
        update={"runtime_ready": False, "schema_version": 40, "commit_sha": None}
    )

    result = evaluate_acceptance(snapshot)
    runtime = next(step for step in result.steps if step.step_key == "runtime")

    assert result.status == "blocked"
    assert runtime.status == "blocked"
    assert runtime.evidence == {"schema_version": 40, "required_schema_version": 41}


def test_missing_security_metric_is_not_treated_as_zero() -> None:
    snapshot = complete_snapshot().model_copy(
        update={"acl_leak_count": None, "citation_failure_count": None}
    )

    result = evaluate_acceptance(snapshot)
    blocked = {step.step_key for step in result.steps if step.status == "blocked"}

    assert {"retrieval_and_acl", "trusted_answer"} <= blocked


def test_explicit_report_failure_is_not_downgraded_to_blocked_by_missing_metric() -> None:
    snapshot = complete_snapshot().model_copy(
        update={
            "retrieval_report_passed": False,
            "acl_leak_count": None,
            "answer_report_passed": False,
            "citation_failure_count": None,
        }
    )

    result = evaluate_acceptance(snapshot)
    steps = {step.step_key: step for step in result.steps}

    assert result.status == "failed"
    assert steps["retrieval_and_acl"].status == "failed"
    assert steps["trusted_answer"].status == "failed"


def test_regression_step_requires_verified_regression_cases() -> None:
    no_cases = complete_snapshot().model_copy(update={"regression_case_count": 0})
    unverified = complete_snapshot().model_copy(update={"regression_unverified_count": 1})

    assert evaluate_acceptance(no_cases).status == "blocked"
    assert evaluate_acceptance(unverified).status == "blocked"
    regression = next(
        step
        for step in evaluate_acceptance(no_cases).steps
        if step.step_key == "evaluation_and_regression"
    )
    assert regression.summary == "缺少已完成验证的回归案例。"
