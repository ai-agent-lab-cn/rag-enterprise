"""从版本化 JSON 文件提供正式检索评测报告的只读查询。"""

from pathlib import Path

from pydantic import ValidationError

from backend.evaluation.report import RetrievalEvaluationReport

from .errors import AppError
from .schemas import EvaluationReportResponse, EvaluationReportSummary


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
        )
