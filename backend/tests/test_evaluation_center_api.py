from datetime import UTC, datetime

from backend.app.main import _capture_online_bad_case, get_evaluation_governance


class _GovernanceStub:
    captured = None

    def capture_online_bad_case(self, **item):
        self.captured = item
        return "case_1234567890abcdef"

    def pipeline_summary(self, knowledge_base_id=None, data_source_id=None):
        return {
            "run_count": 2,
            "added_count": 4,
            "updated_count": 1,
            "deleted_count": 1,
            "skipped_count": 2,
            "failed_count": 1,
            "retry_count": 3,
            "failure_rate": 0.5,
            "average_duration_ms": 20_000,
        }

    def list_bad_cases(self, **_filters):
        return [
            {
                "case_id": "case_1234567890abcdef",
                "source_type": "online",
                "source_record_id": "ans_1",
                "knowledge_base_id": "kb_default",
                "dataset_version": None,
                "question": "为什么没有召回？",
                "expected_source_ids": [],
                "actual_source_ids": [],
                "expected_answer_status": "answered",
                "actual_answer_status": "insufficient_evidence",
                "actual_answer": "资料不足。",
                "failure_stage": "retrieval",
                "root_cause": None,
                "category": "没召回",
                "severity": "high",
                "assignee": None,
                "fix_commit": None,
                "status": "new",
                "regression_added": False,
                "created_at": datetime(2026, 8, 30, tzinfo=UTC),
                "confirmed_at": None,
                "resolved_at": None,
                "updated_at": datetime(2026, 8, 30, tzinfo=UTC),
            }
        ]

    def update_bad_case(self, case_id, update):
        item = self.list_bad_cases()[0]
        return {**item, "case_id": case_id, **update.model_dump(exclude_none=True)}

    def list_acceptance_runs(self, knowledge_base_id=None, limit=50):
        return [self.run_acceptance(knowledge_base_id or "kb_default", "user_admin", True, True, 0)]

    def run_acceptance(self, knowledge_base_id, created_by, retrieval_passed, answer_passed, acl_leak_count):
        return {
            "acceptance_run_id": "acc_1234567890abcdef",
            "knowledge_base_id": knowledge_base_id,
            "status": "blocked",
            "commit_sha": "local-working-tree",
            "schema_version": 14,
            "steps": [
                {
                    "step_key": "external_source",
                    "title": "真实数据源",
                    "status": "blocked",
                    "summary": "缺少 S3 兼容外部数据源。",
                    "evidence": {},
                }
            ],
            "limitations": ["缺少 S3 兼容外部数据源。"],
            "created_by": created_by,
            "created_at": datetime(2026, 8, 30, tzinfo=UTC),
        }


def test_evaluation_center_pipeline_and_bad_case_governance(client) -> None:
    client.app.dependency_overrides[get_evaluation_governance] = lambda: _GovernanceStub()

    pipeline = client.get("/api/evaluation-center/pipeline?knowledge_base_id=kb_default")
    bad_cases = client.get("/api/evaluation-center/bad-cases?knowledge_base_id=kb_default")
    updated = client.put(
        "/api/evaluation-center/bad-cases/case_1234567890abcdef",
        json={"status": "confirmed", "severity": "critical", "root_cause": "过滤条件错误"},
    )

    assert pipeline.status_code == 200
    assert pipeline.json()["average_duration_ms"] == 20_000
    assert bad_cases.status_code == 200
    assert bad_cases.json()[0]["failure_stage"] == "retrieval"
    assert updated.status_code == 200
    assert updated.json()["status"] == "confirmed"
    assert updated.json()["severity"] == "critical"


def test_online_failure_is_captured_with_stable_failure_stage() -> None:
    repository = _GovernanceStub()

    _capture_online_bad_case(
        repository,
        record_id="ans_1",
        knowledge_base_id="kb_default",
        question="为什么没有召回？",
        category="metadata_filter_no_match",
        answer_status="insufficient_evidence",
        answer=None,
        source_ids=[],
    )

    assert repository.captured["failure_stage"] == "retrieval"
    assert repository.captured["category"] == "metadata_filter_no_match"


def test_acceptance_runs_are_readable_and_admin_can_start_one(client) -> None:
    client.app.dependency_overrides[get_evaluation_governance] = lambda: _GovernanceStub()

    missing_scope = client.get("/api/evaluation-center/acceptance-runs")
    listed = client.get("/api/evaluation-center/acceptance-runs?knowledge_base_id=kb_default")
    started = client.post("/api/evaluation-center/acceptance-runs", json={"knowledge_base_id": "kb_default"})

    assert missing_scope.status_code == 422
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "blocked"
    assert started.status_code == 201
    assert started.json()["schema_version"] == 14
