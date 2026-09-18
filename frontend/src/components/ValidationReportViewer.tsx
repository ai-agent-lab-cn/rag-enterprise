import { useState } from "react";
import { api } from "../api";
import type { ValidationReport } from "../types";
import { ValidationReportDialog } from "./ValidationReportDialog";
import { Button } from "./ui/Button";

function shortReportId(value: string) {
  return value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-5)}` : value;
}

export function ValidationReportViewer({
  knowledgeBaseId,
  versionId,
  reportId,
  report,
}: {
  knowledgeBaseId: string;
  versionId: string;
  reportId: string;
  report?: ValidationReport | null;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [loadedReport, setLoadedReport] = useState<ValidationReport | null>(report ?? null);

  const showReport = async () => {
    setOpen(true);
    setError("");
    if (report?.validation_report_id === reportId) {
      setLoadedReport(report);
      setLoading(false);
      return;
    }
    setLoadedReport(null);
    setLoading(true);
    try {
      const reports = await api.listIndexVersionValidations(knowledgeBaseId, versionId);
      const matched = reports.find((item) => item.validation_report_id === reportId) ?? null;
      setLoadedReport(matched);
      if (!matched) setError(`未找到验证报告 ${reportId}。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "验证报告读取失败。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Button
        variant="link"
        className="max-w-full font-mono text-xs"
        title={reportId}
        aria-label={`查看验证报告 ${reportId}`}
        onClick={() => void showReport()}
      >
        {shortReportId(reportId)}
      </Button>
      {open ? (
        <ValidationReportDialog
          open
          reportId={reportId}
          report={loadedReport}
          loading={loading}
          error={error}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
