"""正式检索评测报告的只读查询：合并版本化 JSON 文件与产品内评测运行。"""

from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from backend.evaluation.answer_quality import AnswerEvaluationReport
from backend.evaluation.report import RetrievalEvaluationReport

from .errors import AppError
from .observability import structured_log
from .schemas import (
    AnswerEvaluationReportResponse,
    AnswerEvaluationReportSummary,
    EvaluationCenterOverviewResponse,
    EvaluationReportResponse,
    EvaluationReportSummary,
)


class EvaluationReportRepository:
    """读取已经生成的报告；此类绝不启动模型或评测任务。

    报告有两个来源：仓库里冻结的 JSON 文件（历史基线，随代码走）与 `evaluation_runs`
    里由 Evaluation Worker 写入的产品内正式运行。两者合并成同一个列表，因为发布门禁
    问的问题只有一个——「有没有一份用这套配置跑出来的正式报告」，它不该关心报告是
    从文件读的还是从库里读的。

    同 `report_id` 时数据库记录优先：文件是随代码分发的静态副本，数据库那份带着运行
    事实（谁请求的、跑在哪个候选版本上、指标明细）。
    """

    def __init__(self, reports_path: Path, database_url: str | None = None):
        self.reports_path = reports_path
        self.database_url = database_url

    def list_official(self) -> list[EvaluationReportSummary]:
        return [self._summary(report) for report in self._official_reports()]

    def get_official(self, report_id: str) -> EvaluationReportResponse:
        return self._detail(self.load_official_model(report_id))

    def load_official_model(self, report_id: str) -> RetrievalEvaluationReport:
        """索引放行使用完整报告模型；API 展示仍返回裁剪后的响应模型。"""

        for report in self._official_reports():
            if report.report_id == report_id:
                return report
        raise AppError("EVALUATION_REPORT_NOT_FOUND", "未找到该正式评测报告。", 404)

    def _official_reports(self) -> list[RetrievalEvaluationReport]:
        by_id: dict[str, RetrievalEvaluationReport] = {}
        for path in sorted(self.reports_path.glob("*.json")):
            report = self._load(path)
            if report.official:
                by_id[report.report_id] = report
        for report in self._database_reports():
            by_id[report.report_id] = report
        return sorted(by_id.values(), key=lambda item: item.run_at, reverse=True)

    def _database_reports(self) -> list[RetrievalEvaluationReport]:
        """读取产品内正式评测运行沉淀的报告。

        `official` 与 `passed` 在这里已经分开：筛的是 `official`，不是 `passed`。
        受控运行即使没达到冻结阈值也是可信证据——是否可发布由三层门禁给结论，
        用绝对阈值提前筛掉报告等于让门禁失去「跑过但没达标」这个真实结果。
        """

        if not self.database_url:
            return []
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT evaluation_run_id, report_payload FROM evaluation_runs
                   WHERE evaluation_type='retrieval' AND status='succeeded' AND official
                     AND report_payload IS NOT NULL
                   ORDER BY run_at DESC"""
            ).fetchall()
        reports: list[RetrievalEvaluationReport] = []
        for row in rows:
            try:
                reports.append(RetrievalEvaluationReport.model_validate(row["report_payload"]))
            except ValidationError:
                # 单条 payload 结构过时不能让整个评测中心 500：其余报告仍然可用。
                # 但也不能静默——记一条可检索的日志，否则「报告少了一份」查不出原因。
                structured_log(
                    "evaluation_report.payload_invalid",
                    level=30,
                    evaluation_run_id=str(row["evaluation_run_id"]),
                    result="error",
                )
        return reports

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
            official=report.official,
            passed=report.passed,
            config_fingerprint=report.config_fingerprint,
        )

    @classmethod
    def detail_from_payload(cls, payload: dict[str, object]) -> EvaluationReportResponse:
        """把 `evaluation_runs.report_payload` 直接转成展示模型。

        评测运行详情与只读评测 API 走同一个转换：页面上两处看到的指标结构一致，
        不会出现「运行记录里叫 recall_at_5、报告页里叫 recall5」这种两份映射漂移。
        """

        return cls._detail(RetrievalEvaluationReport.model_validate(payload))

    @classmethod
    def _detail(cls, report: RetrievalEvaluationReport) -> EvaluationReportResponse:
        return EvaluationReportResponse(
            **cls._summary(report).model_dump(),
            parameters=report.parameters,
            query_count=report.query_count,
            recall_at_5=report.recall_at_5.model_dump(),
            recall_at_10=report.recall_at_10.model_dump() if report.recall_at_10 else None,
            vector_mrr=report.vector_mrr.model_dump(),
            rerank_mrr=report.rerank_mrr.model_dump(),
            rerank_recall_at_5=(
                report.rerank_recall_at_5.model_dump() if report.rerank_recall_at_5 else None
            ),
            hybrid_mrr=report.hybrid_mrr.model_dump() if report.hybrid_mrr else None,
            ndcg_at_5=report.ndcg_at_5.model_dump() if report.ndcg_at_5 else None,
            ndcg_at_10=report.ndcg_at_10.model_dump() if report.ndcg_at_10 else None,
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
            official=report.official,
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
