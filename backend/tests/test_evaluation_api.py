import json
from pathlib import Path

from backend.app.evaluation_reports import EvaluationReportRepository
from backend.app.main import get_evaluation_reports, get_service


def _write_report(target: Path, **changes) -> dict:
    source = Path("backend/evaluation/reports/retrieval_v1_optimized.json")
    report = json.loads(source.read_text(encoding="utf-8"))
    report.update(changes)
    target.write_text(json.dumps(report), encoding="utf-8")
    return report


def _use_reports(client, reports_path: Path) -> None:
    client.app.dependency_overrides[get_evaluation_reports] = lambda: EvaluationReportRepository(
        reports_path
    )


def test_evaluation_list_is_empty_without_reports_and_does_not_initialize_models(client, tmp_path) -> None:
    def fail_service_initialization() -> None:
        raise AssertionError("评测报告查询不得初始化 RAG 模型")

    client.app.dependency_overrides[get_service] = fail_service_initialization
    _use_reports(client, tmp_path)

    response = client.get("/api/evaluations")

    assert response.status_code == 200
    assert response.json() == []


def test_evaluation_list_only_exposes_official_reports_in_latest_first_order(client, tmp_path) -> None:
    older = _write_report(
        tmp_path / "older.json",
        report_id="official-older",
        run_at="2026-08-07T00:00:00Z",
    )
    _write_report(
        tmp_path / "newer.json",
        report_id="official-newer",
        run_at="2026-08-08T00:00:00Z",
    )
    _write_report(tmp_path / "fake.json", report_id="test-double", official=False)
    _use_reports(client, tmp_path)

    response = client.get("/api/evaluations")

    assert response.status_code == 200
    payload = response.json()
    assert [item["report_id"] for item in payload] == ["official-newer", "official-older"]
    assert payload[1] == {
        "report_id": "official-older",
        "dataset_id": older["dataset_id"],
        "dataset_version": older["dataset_version"],
        "commit": older["commit"],
        "run_at": "2026-08-07T00:00:00Z",
        "models": older["models"],
        "passed": True,
    }


def test_evaluation_detail_returns_complete_context_metrics_and_conclusions(client, tmp_path) -> None:
    report = _write_report(tmp_path / "official.json", report_id="official-report")
    _use_reports(client, tmp_path)

    response = client.get("/api/evaluations/official-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["commit"] == report["commit"]
    assert payload["models"] == report["models"]
    assert payload["parameters"] == report["parameters"]
    assert payload["query_count"] == 20
    assert payload["recall_at_5"] == report["recall_at_5"]
    assert payload["vector_mrr"] == report["vector_mrr"]
    assert payload["rerank_mrr"] == report["rerank_mrr"]
    assert payload["passed"] is True


def test_evaluation_detail_hides_non_official_and_unknown_reports(client, tmp_path) -> None:
    _write_report(tmp_path / "fake.json", report_id="test-double", official=False)
    _use_reports(client, tmp_path)

    hidden = client.get("/api/evaluations/test-double")
    missing = client.get("/api/evaluations/unknown")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == "EVALUATION_REPORT_NOT_FOUND"
    assert missing.json()["error"]["code"] == "EVALUATION_REPORT_NOT_FOUND"


def test_invalid_evaluation_report_returns_stable_error(client, tmp_path) -> None:
    (tmp_path / "broken.json").write_text("{invalid", encoding="utf-8")
    _use_reports(client, tmp_path)

    response = client.get("/api/evaluations")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "EVALUATION_REPORT_INVALID",
            "message": "评测报告格式无效。",
            "details": {"filename": "broken.json"},
        }
    }


def test_answer_evaluation_api_only_exposes_official_report(client, tmp_path) -> None:
    answers = tmp_path / "answers"
    answers.mkdir()
    source = Path("backend/evaluation/reports/answers/answer_v1_baseline.json")
    official = json.loads(source.read_text(encoding="utf-8"))
    (answers / "official.json").write_text(json.dumps(official), encoding="utf-8")
    hidden = {**official, "report_id": "answer-hidden", "official": False}
    (answers / "hidden.json").write_text(json.dumps(hidden), encoding="utf-8")
    _use_reports(client, tmp_path)

    listed = client.get("/api/evaluations/answers/reports")
    detail = client.get(f"/api/evaluations/answers/reports/{official['report_id']}")
    missing = client.get("/api/evaluations/answers/reports/answer-hidden")

    assert listed.status_code == 200
    assert [item["report_id"] for item in listed.json()] == [official["report_id"]]
    assert detail.status_code == 200
    assert detail.json()["case_count"] == 30
    assert detail.json()["metrics"]["answer_correctness"]["value"] == 1.0
    assert detail.json()["metrics"]["source_conflict_accuracy"]["value"] == 1.0
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ANSWER_EVALUATION_REPORT_NOT_FOUND"


def test_evaluation_center_overview_unifies_latest_official_reports(client, tmp_path) -> None:
    _write_report(tmp_path / "retrieval.json", report_id="retrieval-official")
    answers = tmp_path / "answers"
    answers.mkdir()
    source = Path("backend/evaluation/reports/answers/answer_v1_baseline.json")
    answers.joinpath("answer.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    _use_reports(client, tmp_path)

    response = client.get("/api/evaluation-center/overview")

    assert response.status_code == 200
    assert response.json()["retrieval_report"]["report_id"] == "retrieval-official"
    assert response.json()["answer_report"]["dataset_id"] == "rag-enterprise-answer-quality"
    assert response.json()["passed"] is True
