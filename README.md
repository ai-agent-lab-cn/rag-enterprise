# RongRAG Studio

一个面向个人项目资料的可解释 RAG 问答系统。它不仅返回答案，也展示答案来自哪个文件、哪一页或段落，以及向量召回、CrossEncoder 精排和生成分别花了多长时间。

![RongRAG Studio 查询界面](assets/rongrag-studio-ui.jpg)

![RAG 检索与生成链路](assets/rag-flow.png)

## 项目亮点

- Markdown、TXT、PDF 多格式解析，稳定文档 ID 与完整来源 metadata
- 本地中文 Embedding + ChromaDB 持久化向量召回
- CrossEncoder 精排，同时展示粗召回与精排分数
- Gemini 生成带 `[来源 N]` 标签的答案，未配置 Key 时仍可完成检索
- FastAPI 类型化接口与 React/TypeScript 交互界面
- Recall@K、MRR 评测脚本，以及检索/精排/生成分阶段延迟
- 上传文件、向量索引和密钥默认不进入 Git

## 工作流程

```text
MD / TXT / PDF
      │
      ▼
解析与重叠切片 ──► 中文 Embedding ──► ChromaDB 持久化索引
                                             │
用户问题 ──► 查询向量 ──► top-k 粗召回 ──► CrossEncoder 精排
                                             │
                                             ▼
                                来源标签 + Prompt ──► Gemini ──► 答案与证据
```

## 技术栈

| 层级 | 技术 |
| --- | --- |
| Web | React、TypeScript、Vite |
| API | FastAPI、Pydantic |
| 文档 | pypdf、UTF-8 Markdown/TXT 解析 |
| 检索 | text2vec 中文 Embedding、ChromaDB |
| 精排 | sentence-transformers CrossEncoder |
| 生成 | Google Gemini（环境变量配置） |
| 质量 | pytest、Vitest、ESLint、Ruff |

## 快速开始

环境要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 20+。

```bash
git clone https://github.com/rongrongzang/rag_d.git
cd rag_d
cp .env.example .env
uv sync --dev
cd frontend && npm install && cd ..
```

如需生成答案，在 `.env` 中填写从 [Google AI Studio](https://aistudio.google.com/apikey) 获取的密钥：

```env
GEMINI_API_KEY=your_key_here
```

分别启动后端和前端：

```bash
uv run uvicorn backend.app.main:app --reload
```

```bash
cd frontend
npm run dev
```

打开 <http://localhost:5173>，上传 `knowledge/project-profile.md` 后即可使用。API 文档位于 <http://localhost:8000/api/docs>。

## API

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| `POST` | `/api/documents` | 上传并索引 MD、TXT 或 PDF |
| `GET` | `/api/documents` | 获取已索引文档及 chunk 数量 |
| `DELETE` | `/api/documents/{id}` | 删除文档、向量与本地上传文件 |
| `POST` | `/api/query` | 检索、精排并生成带来源答案 |
| `GET` | `/api/health` | 检查索引与模型配置状态 |

查询请求示例：

```json
{
  "question": "项目如何保证回答可追溯？",
  "retrieve_k": 10,
  "rerank_k": 5
}
```

## 评测

先通过 Web 或 API 索引 `knowledge/project-profile.md`，再运行：

```bash
uv run python -m evaluations.evaluate
```

脚本基于 `evaluations/questions.json` 计算向量检索的 Recall@10、MRR 和精排后的 MRR。仓库只提供可复现的评测方法，不提交未经实际运行的结果数字。

当前演示知识库的实测结果（2026-08-02，4 个标注问题）：

| 指标 | 结果 |
| --- | ---: |
| 向量召回 Recall@10 | 1.000 |
| 向量排序 MRR | 0.688 |
| CrossEncoder 精排 MRR | 1.000 |

小规模演示集仅用于验证评测链路，不代表通用数据集表现。

## 测试与质量检查

```bash
uv run pytest
uv run ruff check backend evaluations
cd frontend
npm test
npm run lint
npm run build
```

## 数据与隐私

- `.env`、`data/uploads/` 和 `data/chroma/` 已加入 `.gitignore`。
- 演示资料由项目作者编写，不包含真实电话、邮箱或访问令牌。
- 不建议把包含个人敏感信息的索引目录或 API 原始响应公开提交。

## 独立实现与致谢

本项目是为个人作品集从零设计和实现的工程项目，不宣称原创 RAG 算法。学习过程中参考了[马克的技术工作坊：使用 Python 构建 RAG 系统](https://github.com/MarkTechStation/VideoCode/tree/main/%E4%BD%BF%E7%94%A8Python%E6%9E%84%E5%BB%BARAG%E7%B3%BB%E7%BB%9F/rag)所介绍的通用流程。原仓库未提供开源许可证，因此本项目未复制其代码、README 文案或示例文档，仅在此注明概念学习来源。

## License

本仓库暂未添加开源许可证，默认保留所有权利。如需授权复用，请先联系仓库作者。
