"""正式检索评测报告的只读查询：合并版本化 JSON 文件与产品内评测运行。"""

from datetime import UTC, datetime
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
    EvaluationReportAssociationsResponse,
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
        required_scopes = ["retrieval", "answer"]
        available_scopes = [
            scope
            for scope, report in (("retrieval", latest_retrieval), ("answer", latest_answer))
            if report is not None
        ]
        missing_scopes = [scope for scope in required_scopes if scope not in available_scopes]
        failed_scopes = [
            scope
            for scope, report in (("retrieval", latest_retrieval), ("answer", latest_answer))
            if report is not None and not report.passed
        ]
        status = "failed" if failed_scopes else "incomplete" if missing_scopes else "passed"
        return EvaluationCenterOverviewResponse(
            passed=status == "passed",
            status=status,
            required_scopes=required_scopes,
            available_scopes=available_scopes,
            missing_scopes=missing_scopes,
            failed_scopes=failed_scopes,
            generated_at=datetime.now(UTC),
            retrieval_report=latest_retrieval,
            answer_report=latest_answer,
            report_count=len(retrieval) + len(answers),
        )

    def report_associations(
        self,
        report_id: str,
        accessible_knowledge_base_ids: set[str] | None = None,
    ) -> EvaluationReportAssociationsResponse:
        """返回检索报告的来源版本、可匹配版本与门禁使用记录。

        关联完全从已有运行事实、配置指纹和验证报告推导，不新增一套关系表。历史文件报告
        没有产品内运行记录时，来源版本明确为空；只要配置指纹可用，仍可以展示当前兼容版本。
        """

        report = self.load_official_model(report_id)
        empty = EvaluationReportAssociationsResponse(
            report_id=report_id,
            evaluation_type="retrieval",
        )
        if not self.database_url or accessible_knowledge_base_ids == set():
            return empty

        access_clause = ""
        access_values: tuple[object, ...] = ()
        if accessible_knowledge_base_ids is not None:
            access_clause = " AND iv.knowledge_base_id = ANY(%s)"
            access_values = (list(accessible_knowledge_base_ids),)

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            origin = connection.execute(
                f"""SELECT er.evaluation_run_id, iv.knowledge_base_id,
                           iv.index_version_id, iv.version_no, iv.status,
                           iv.config_fingerprint
                    FROM evaluation_runs er
                    JOIN index_versions iv ON iv.index_version_id=er.index_version_id
                    WHERE er.evaluation_type='retrieval'
                      AND er.report_payload->>'report_id'=%s{access_clause}
                    ORDER BY er.finished_at DESC NULLS LAST, er.created_at DESC
                    LIMIT 1""",  # noqa: S608 -- access_clause 只来自上面的固定字符串。
                (report_id, *access_values),
            ).fetchone()

            compatible: list[dict[str, object]] = []
            if report.config_fingerprint:
                compatible = connection.execute(
                    f"""SELECT iv.knowledge_base_id, iv.index_version_id, iv.version_no,
                               iv.status, iv.config_fingerprint
                        FROM index_versions iv
                        WHERE iv.config_fingerprint=%s{access_clause}
                        ORDER BY iv.created_at DESC""",  # noqa: S608
                    (report.config_fingerprint, *access_values),
                ).fetchall()

            validations = connection.execute(
                f"""SELECT vr.validation_report_id, iv.knowledge_base_id,
                           vr.index_version_id, vr.status, vr.created_at
                    FROM validation_reports vr
                    JOIN index_versions iv ON iv.index_version_id=vr.index_version_id
                    WHERE vr.evaluation_set_version=%s{access_clause}
                    ORDER BY vr.created_at DESC""",  # noqa: S608
                (report_id, *access_values),
            ).fetchall()

        origin_payload = None
        origin_run_id = None
        if origin is not None:
            origin_run_id = str(origin["evaluation_run_id"])
            origin_payload = {
                "knowledge_base_id": str(origin["knowledge_base_id"]),
                "index_version_id": str(origin["index_version_id"]),
                "version_no": int(origin["version_no"]) if origin["version_no"] else None,
                "status": str(origin["status"]),
                "config_fingerprint": (
                    str(origin["config_fingerprint"]) if origin["config_fingerprint"] else None
                ),
            }

        return EvaluationReportAssociationsResponse(
            report_id=report_id,
            evaluation_type="retrieval",
            origin_evaluation_run_id=origin_run_id,
            origin_version=origin_payload,
            compatible_versions=[dict(item) for item in compatible],
            validation_usages=[dict(item) for item in validations],
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
