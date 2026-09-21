from datetime import UTC, datetime

from backend.app.main import (
    _capture_online_bad_case,
    get_evaluation_governance,
    get_evaluation_reports,
)


class _GovernanceStub:
    captured = None

    def __init__(self):
        self.pipeline_scope = None
        self.rag_scope = None
        self.bad_case_scope = None
        self.acceptance_scope = None

    def capture_online_bad_case(self, **item):
        self.captured = item
        return "case_1234567890abcdef"

    def pipeline_summary(self, knowledge_base_id=None, data_source_id=None, knowledge_base_ids=None):
        self.pipeline_scope = knowledge_base_ids
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

    def rag_pipeline_summary(self, knowledge_base_id=None, knowledge_base_ids=None):
        self.rag_scope = knowledge_base_ids
        return []

    def list_bad_cases(self, **filters):
        self.bad_case_scope = filters.get("knowledge_base_ids")
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
                "regression_evaluation_run_id": None,
                "regression_passed": None,
                "regression_run_at": None,
                "created_at": datetime(2026, 8, 30, tzinfo=UTC),
                "confirmed_at": None,
                "resolved_at": None,
                "updated_at": datetime(2026, 8, 30, tzinfo=UTC),
            }
        ]

    def update_bad_case(self, case_id, update):
        item = self.list_bad_cases()[0]
        return {**item, "case_id": case_id, **update.model_dump(exclude_none=True)}

    def get_bad_case(self, case_id):
        return {
            **self.list_bad_cases()[0],
            "case_id": case_id,
            "status": "resolved",
            "fix_commit": "abcdef1",
        }

    def run_bad_case_regression(self, case_id, **evidence):
        self.regression_evidence = evidence
        return {
            **self.get_bad_case(case_id),
            "status": "regression_added",
            "regression_added": True,
            "regression_evaluation_run_id": "eval_regression_1",
            "regression_passed": True,
            "regression_run_at": datetime(2026, 8, 30, tzinfo=UTC),
        }

    def list_acceptance_runs(self, knowledge_base_id=None, limit=50):
        return [self._acceptance_payload(knowledge_base_id or "kb_default", "user_admin")]

    def run_acceptance(self, knowledge_base_id, created_by):
        self.acceptance_scope = knowledge_base_id
        return self._acceptance_payload(knowledge_base_id, created_by)

    @staticmethod
    def _acceptance_payload(knowledge_base_id, created_by):
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


class _ReportsStub:
    def report_associations(self, report_id, accessible_knowledge_base_ids=None):
        return {
            "report_id": report_id,
            "evaluation_type": "retrieval",
            "origin_evaluation_run_id": "eval_123",
            "origin_version": {
                "knowledge_base_id": "kb_default",
                "index_version_id": "iv_candidate",
                "version_no": 2,
                "status": "validating",
                "config_fingerprint": "a" * 64,
            },
            "compatible_versions": [
                {
                    "knowledge_base_id": "kb_default",
                    "index_version_id": "iv_candidate",
                    "version_no": 2,
                    "status": "validating",
                    "config_fingerprint": "a" * 64,
                }
            ],
            "validation_usages": [
                {
                    "validation_report_id": "vr_123",
                    "knowledge_base_id": "kb_default",
                    "index_version_id": "iv_candidate",
                    "status": "pass",
                    "created_at": datetime(2026, 8, 30, tzinfo=UTC),
                }
            ],
        }


def test_evaluation_center_pipeline_and_bad_case_governance(client) -> None:
    repository = _GovernanceStub()
    client.app.dependency_overrides[get_evaluation_governance] = lambda: repository

    pipeline = client.get("/api/evaluation-center/pipeline?knowledge_base_id=kb_default")
    bad_cases = client.get("/api/evaluation-center/bad-cases?knowledge_base_id=kb_default")
    updated = client.put(
        "/api/evaluation-center/bad-cases/case_1234567890abcdef",
        json={"status": "confirmed", "severity": "critical", "root_cause": "过滤条件错误"},
    )

    assert pipeline.status_code == 200
    assert pipeline.json()["average_duration_ms"] == 20_000
    assert pipeline.json()["rag_profiles"] == []
    assert bad_cases.status_code == 200
    assert bad_cases.json()[0]["failure_stage"] == "retrieval"
    assert updated.status_code == 200
    assert updated.json()["status"] == "confirmed"
    assert updated.json()["severity"] == "critical"


def test_member_metrics_and_bad_cases_are_limited_to_accessible_knowledge_bases(client) -> None:
    repository = _GovernanceStub()
    client.app.dependency_overrides[get_evaluation_governance] = lambda: repository
    password = "correct-horse-battery-staple"
    member = client.post(
        "/api/members",
        json={
            "username": "evaluation-member",
            "password": password,
            "display_name": "评测成员",
            "role": "member",
        },
    )
    member_id = member.json()["user_id"]
    assert client.put(f"/api/knowledge-bases/kb_default/members/{member_id}").status_code == 204
    secret = client.post(
        "/api/knowledge-bases",
        json={"name": "未授权知识库", "description": "不可见", "apply_default_category_template": False},
    )
    secret_id = secret.json()["knowledge_base_id"]
    login = client.post(
        "/api/auth/login",
        json={"username": "evaluation-member", "password": password},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    pipeline = client.get("/api/evaluation-center/pipeline", headers=headers)
    bad_cases = client.get("/api/evaluation-center/bad-cases", headers=headers)
    denied_pipeline = client.get(
        f"/api/evaluation-center/pipeline?knowledge_base_id={secret_id}", headers=headers
    )
    denied_bad_cases = client.get(
        f"/api/evaluation-center/bad-cases?knowledge_base_id={secret_id}", headers=headers
    )

    assert pipeline.status_code == 200
    assert bad_cases.status_code == 200
    assert repository.pipeline_scope == {"kb_default"}
    assert repository.rag_scope == {"kb_default"}
    assert repository.bad_case_scope == {"kb_default"}
    assert denied_pipeline.status_code == 404
    assert denied_bad_cases.status_code == 404


def test_evaluation_report_associations_expose_origin_compatibility_and_validation_use(client) -> None:
    client.app.dependency_overrides[get_evaluation_reports] = lambda: _ReportsStub()

    response = client.get("/api/evaluation-center/reports/retrieval-official/associations")

    assert response.status_code == 200
    assert response.json()["origin_evaluation_run_id"] == "eval_123"
    assert response.json()["origin_version"]["index_version_id"] == "iv_candidate"
    assert response.json()["compatible_versions"][0]["version_no"] == 2
    assert response.json()["validation_usages"][0]["validation_report_id"] == "vr_123"


def test_bad_case_regression_runs_real_query_and_binds_evaluation_evidence(
    client, fake_service
) -> None:
    repository = _GovernanceStub()
    client.app.dependency_overrides[get_evaluation_governance] = lambda: repository
    original_query = fake_service.query
    captured = {}

    def query(*args, **kwargs):
        captured["access"] = args[5]
        return original_query(*args, **kwargs)

    fake_service.query = query

    response = client.post(
        "/api/evaluation-center/bad-cases/case_1234567890abcdef/regressions"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "regression_added"
    assert response.json()["regression_evaluation_run_id"] == "eval_regression_1"
    assert repository.regression_evidence["actual_answer_status"] == "answered"
    assert repository.regression_evidence["actual_source_ids"] == ["doc_test"]
    assert captured["access"] is None


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
    repository = _GovernanceStub()
    client.app.dependency_overrides[get_evaluation_governance] = lambda: repository

    missing_scope = client.get("/api/evaluation-center/acceptance-runs")
    listed = client.get("/api/evaluation-center/acceptance-runs?knowledge_base_id=kb_default")
    started = client.post("/api/evaluation-center/acceptance-runs", json={"knowledge_base_id": "kb_default"})

    assert missing_scope.status_code == 422
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "blocked"
    assert started.status_code == 201
    assert started.json()["schema_version"] == 14
    assert repository.acceptance_scope == "kb_default"
