import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AnswerPanel } from "./components/AnswerPanel";
import { DocumentPanel } from "./components/DocumentPanel";
import type { DocumentInfo, QueryResult } from "./types";
import "./styles.css";

const EXAMPLE_QUESTIONS = ["这个项目解决了什么问题？", "系统采用了哪些技术？", "如何评估检索效果？"];

export default function App() {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [error, setError] = useState("");

  const refreshDocuments = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取文档列表。");
    }
  }, []);

  useEffect(() => {
    let active = true;
    api.listDocuments().then(
      (items) => { if (active) setDocuments(items); },
      (reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取文档列表。");
      },
    );
    return () => { active = false; };
  }, []);

  const upload = async (file: File) => {
    setUploading(true);
    setError("");
    try {
      await api.uploadDocument(file);
      await refreshDocuments();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上传失败。");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (documentId: string) => {
    setError("");
    try {
      await api.deleteDocument(documentId);
      setDocuments((current) => current.filter((item) => item.document_id !== documentId));
      setResult(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除失败。");
    }
  };

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) return;
    setQuerying(true);
    setError("");
    try {
      setResult(await api.query(trimmedQuestion));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "查询失败。");
    } finally {
      setQuerying(false);
    }
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="RongRAG Studio 首页">
          <span className="brand-symbol">R</span>
          <span><strong>RongRAG</strong><small>STUDIO</small></span>
        </a>
        <div className="system-state"><span /> 本地检索引擎</div>
      </header>

      <div className="workspace" id="top">
        <DocumentPanel documents={documents} loading={uploading} onUpload={upload} onDelete={remove} />
        <section className="conversation">
          <div className="hero-copy">
            <span className="eyebrow">RETRIEVE · RERANK · RESPOND</span>
            <h1>让你的项目资料，<br /><em>自己给出答案。</em></h1>
            <p>每一个结论都能追溯到原文，每一次检索都展示真实分数与延迟。</p>
          </div>

          {error ? <div className="error-banner" role="alert">{error}</div> : null}
          <AnswerPanel result={result} loading={querying} />

          <div className="examples" aria-label="示例问题">
            {EXAMPLE_QUESTIONS.map((example) => (
              <button type="button" key={example} onClick={() => setQuestion(example)}>{example}</button>
            ))}
          </div>

          <form className="question-box" onSubmit={ask}>
            <label className="sr-only" htmlFor="question">向知识库提问</label>
            <textarea
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="例如：这个项目如何保证回答可追溯？"
              rows={2}
              maxLength={2000}
            />
            <div className="question-footer">
              <span>{documents.length ? `正在检索 ${documents.length} 份资料` : "请先添加资料"}</span>
              <button type="submit" disabled={querying || !question.trim() || documents.length === 0}>
                {querying ? "思考中" : "提问"} <span aria-hidden="true">→</span>
              </button>
            </div>
          </form>
        </section>
      </div>
    </main>
  );
}
