"""从版本化 JSON 文件提供正式检索评测报告的只读查询。"""

from pathlib import Path

from pydantic import ValidationError

from backend.evaluation.answer_quality import AnswerEvaluationReport
from backend.evaluation.report import RetrievalEvaluationReport

from .errors import AppError
from .schemas import (
    AnswerEvaluationReportResponse,
    AnswerEvaluationReportSummary,
    EvaluationCenterOverviewResponse,
    EvaluationReportResponse,
    EvaluationReportSummary,
)


class EvaluationReportRepository:
    """读取已经离线生成的报告；此类绝不启动模型或评测任务。"""

    def __init__(self, reports_path: Path):
        self.reports_path = reports_path

    def list_official(self) -> list[EvaluationReportSummary]:
        reports = [self._load(path) for path in sorted(self.reports_path.glob("*.json"))]
        official = [report for report in reports if report.official]
        newest_first = sorted(official, key=lambda item: item.run_at, reverse=True)
        return [self._summary(report) for report in newest_first]

    def get_official(self, report_id: str) -> EvaluationReportResponse:
        for path in sorted(self.reports_path.glob("*.json")):
            report = self._load(path)
            if report.official and report.report_id == report_id:
                return self._detail(report)
        raise AppError("EVALUATION_REPORT_NOT_FOUND", "未找到该正式评测报告。", 404)

    def list_official_answers(self) -> list[AnswerEvaluationReportSummary]:
        """回答报告独立存放，只公开经过人工复核后标记 official 的正式报告。"""
        reports = [
            self._load_answer(path)
            for path in sorted((self.reports_path / "answers").glob("*.json"))
            if "human_review" not in path.name
        ]
        official = [report for report in reports if report.official]
        newest_first = sorted(official, key=lambda item: item.run_at, reverse=True)
        return [self._answer_summary(report) for report in newest_first]

    def get_official_answer(self, report_id: str) -> AnswerEvaluationReportResponse:
        for path in sorted((self.reports_path / "answers").glob("*.json")):
            if "human_review" in path.name:
                continue
            report = self._load_answer(path)
            if report.official and report.report_id == report_id:
                return self._answer_detail(report)
        raise AppError("ANSWER_EVALUATION_REPORT_NOT_FOUND", "未找到该正式回答评测报告。", 404)

    def center_overview(self) -> EvaluationCenterOverviewResponse:
        retrieval = self.list_official()
        answers = self.list_official_answers()
        latest_retrieval = retrieval[0] if retrieval else None
        latest_answer = answers[0] if answers else None
        reports = [item for item in (latest_retrieval, latest_answer) if item is not None]
        return EvaluationCenterOverviewResponse(
            passed=bool(reports) and all(item.passed for item in reports),
            retrieval_report=latest_retrieval,
            answer_report=latest_answer,
            report_count=len(retrieval) + len(answers),
        )

    @staticmethod
    def _load(path: Path) -> RetrievalEvaluationReport:
        try:
            return RetrievalEvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            # 不把解析器内部信息或文件内容返回浏览器，只暴露稳定且可定位的文件名。
            raise AppError(
                "EVALUATION_REPORT_INVALID",
                "评测报告格式无效。",
                500,
                {"filename": path.name},
            ) from exc

    @staticmethod
    def _load_answer(path: Path) -> AnswerEvaluationReport:
        try:
            return AnswerEvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise AppError(
                "ANSWER_EVALUATION_REPORT_INVALID",
                "回答评测报告格式无效。",
                500,
                {"filename": path.name},
            ) from exc

    @staticmethod
    def _summary(report: RetrievalEvaluationReport) -> EvaluationReportSummary:
        return EvaluationReportSummary(
            report_id=report.report_id,
            dataset_id=report.dataset_id,
            dataset_version=report.dataset_version,
            commit=report.commit,
            run_at=report.run_at,
            models=report.models,
            passed=report.passed,
        )

    @classmethod
    def _detail(cls, report: RetrievalEvaluationReport) -> EvaluationReportResponse:
        return EvaluationReportResponse(
            **cls._summary(report).model_dump(),
            parameters=report.parameters,
            query_count=report.query_count,
            recall_at_5=report.recall_at_5.model_dump(),
            vector_mrr=report.vector_mrr.model_dump(),
            rerank_mrr=report.rerank_mrr.model_dump(),
            rerank_recall_at_5=(
                report.rerank_recall_at_5.model_dump() if report.rerank_recall_at_5 else None
            ),
            hybrid_mrr=report.hybrid_mrr.model_dump() if report.hybrid_mrr else None,
            ndcg_at_5=report.ndcg_at_5.model_dump() if report.ndcg_at_5 else None,
            metadata_filter_accuracy=(
                report.metadata_filter_accuracy.model_dump()
                if report.metadata_filter_accuracy
                else None
            ),
            query_rewrite_success_rate=(
                report.query_rewrite_success_rate.model_dump()
                if report.query_rewrite_success_rate
                else None
            ),
            query_rewrite_fallback_rate=(
                report.query_rewrite_fallback_rate.model_dump()
                if report.query_rewrite_fallback_rate
                else None
            ),
            no_result_rate=report.no_result_rate.model_dump() if report.no_result_rate else None,
            acl_leak_count=report.acl_leak_count,
        )

    @staticmethod
    def _answer_summary(report: AnswerEvaluationReport) -> AnswerEvaluationReportSummary:
        return AnswerEvaluationReportSummary(
            report_id=report.report_id,
            dataset_id=report.dataset_id,
            dataset_version=report.dataset_version,
            commit=report.commit,
            run_at=report.run_at,
            prompt_version=report.prompt_version,
            models=report.models,
            passed=report.passed,
        )

    @classmethod
    def _answer_detail(cls, report: AnswerEvaluationReport) -> AnswerEvaluationReportResponse:
        metrics = {key: value.model_dump() if value else None for key, value in report.metrics}
        if metrics.get("source_conflict_accuracy") is None:
            conflict_results = [
                item for item in report.deterministic_results
                if item.expected_status == "source_conflict"
            ]
            value = (
                sum(item.status_correct for item in conflict_results) / len(conflict_results)
                if conflict_results
                else 1.0
            )
            metrics["source_conflict_accuracy"] = {
                "value": value,
                "threshold": 0.90,
                "direction": "minimum",
                "baseline": None,
                "passed": value >= 0.90,
                "regressed": False,
            }
        return AnswerEvaluationReportResponse(
            **cls._answer_summary(report).model_dump(),
            prompt_hash=report.prompt_hash,
            parameters=report.parameters,
            case_count=report.case_count,
            metrics=metrics,
        )
