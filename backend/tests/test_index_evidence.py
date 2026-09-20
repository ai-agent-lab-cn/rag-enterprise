from datetime import UTC, datetime

from backend.app.index_evidence import assemble_index_evidence_chain

NOW = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)


def test_assembles_version_to_activation_evidence_chain() -> None:
    result = assemble_index_evidence_chain(
        knowledge_base_id="kb_1",
        index_version={
            "index_version_id": "iv_1",
            "version_no": 3,
            "status": "active",
            "config_fingerprint": "a" * 64,
            "evaluation_report_id": "rep_1",
            "validation_report_id": "vr_1",
            "activated_at": NOW,
        },
        evaluation_run={
            "evaluation_run_id": "er_1",
            "status": "succeeded",
            "official": True,
            "passed": True,
            "config_fingerprint": "a" * 64,
            "report_payload": {"report_id": "rep_1", "run_at": NOW.isoformat()},
            "created_at": NOW,
        },
        validation_report={
            "validation_report_id": "vr_1",
            "status": "pass",
            "report_source": "standard",
            "evaluation_set_version": "rep_1",
            "retrieval_result": {
                "evaluation_report_id": "rep_1",
                "checks": [{"check_key": "config_fingerprint_matches", "status": "pass"}],
            },
            "created_at": NOW,
        },
        activation_event={
            "event_id": "ile_1",
            "event_type": "activated",
            "actor_id": "admin",
            "validation_report_id": "vr_1",
            "created_at": NOW,
        },
    )

    assert result["knowledge_base_id"] == "kb_1"
    assert result["index_version_id"] == "iv_1"
    assert result["version"]["status"] == "active"
    assert result["evaluation_run"]["evaluation_run_id"] == "er_1"
    assert result["formal_report"]["report_id"] == "rep_1"
    assert result["validation_report"]["validation_report_id"] == "vr_1"
    assert result["activation"]["event_id"] == "ile_1"


def test_keeps_missing_links_explicit_instead_of_inventing_evidence() -> None:
    result = assemble_index_evidence_chain(
        knowledge_base_id="kb_1",
        index_version={
            "index_version_id": "iv_legacy",
            "version_no": 1,
            "status": "retired",
            "config_fingerprint": "",
            "evaluation_report_id": None,
            "validation_report_id": None,
            "activated_at": None,
        },
        evaluation_run=None,
        validation_report=None,
        activation_event=None,
    )

    assert result["evaluation_run"] is None
    assert result["formal_report"] is None
    assert result["validation_report"] is None
    assert result["activation"] is None
