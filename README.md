# RongRAG Studio

一个面向个人项目资料的可解释 RAG 问答系统。它不仅返回答案，也展示答案来自哪个文件、哪一页或段落，以及向量召回、CrossEncoder 精排和生成分别花了多长时间。

![RongRAG Studio 查询界面](assets/rongrag-studio-ui.jpg)

![RAG 检索与生成链路](assets/rag-flow.png)

## 项目亮点

- Markdown、TXT、PDF 多格式解析，稳定文档 ID 与完整来源 metadata
- 本地中文 Embedding + PostgreSQL/pgvector 向量召回，按索引版本隔离
- CrossEncoder 精排，同时展示粗召回与精排分数
- Gemini 生成带 `[来源 N]` 标签的答案，未配置 Key 时仍可完成检索
- FastAPI 类型化接口与 React/TypeScript 交互界面
- 知识库级数据源治理：S3 连接测试、增量同步、进度、失败重试和同步记录
- Recall@K、MRR 评测脚本，以及检索/精排/生成分阶段延迟
- 上传文件、向量索引和密钥默认不进入 Git

## 工作流程

```text
MD / TXT / PDF
      │
      ▼
解析与重叠切片 ──► 中文 Embedding ──► pgvector 索引（按索引版本隔离）
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
| 检索 | text2vec 中文 Embedding、PostgreSQL 16 + pgvector |
| 精排 | sentence-transformers CrossEncoder |
| 生成 | Google Gemini（环境变量配置） |
| 数据源 | 本地目录、S3 兼容对象存储（minio SDK） |
| 质量 | pytest、Vitest、ESLint、Ruff |

## 快速开始

环境要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js `^20.19.0` 或 `>=22.12.0`。

Node 版本下限由 `rolldown` 的 `engines` 决定。低于该下限时 npm 会**静默跳过**它的平台
原生 binding（optional dependency），`npm test` 与 `npm run build` 随即报
`Cannot find native binding`，且退出码仍是 0。删除 `package-lock.json` 重装无效——
锁文件里 15 个平台 binding 条目一直都在，被跳过的原因只是 engine 不匹配。

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

首次打开 <http://localhost:5173> 时，页面会引导创建首位管理员；初始化只能执行一次，之后
使用登录页进入工作台。也可在 API 文档 <http://localhost:8000/api/docs> 调用
`POST /api/auth/bootstrap` 或 `POST /api/auth/login`，再把 Bearer 令牌填入右上角的
**Authorize**。健康检查和初始化状态可匿名访问，其余业务 API 默认要求有效会话。当前提供
最小认证入口；完整成员管理、系统状态和审计页面由后续 V4 页面任务实现。

生产配置应设置 `APP_ENVIRONMENT=production`，并把 `FRONTEND_ORIGIN` 设置为明确的
HTTPS 来源；多个来源使用英文逗号分隔。生产模式会关闭 OpenAPI 文档入口，且拒绝通配或
非 HTTPS 跨域来源。上传、请求体、登录频率、高成本请求频率及并发上限均可通过
`.env.example` 中的对应变量调整。

### 使用 Docker Compose

本机安装 Docker 后，可构建并启动前后端容器：

```bash
docker compose up --build -d
```

打开 <http://localhost:5173> 即可访问界面。前端会把 `/api` 请求转发到后端；健康检查地址为 <http://localhost:5173/api/health>。

如需启用 Gemini 生成，先在仓库根目录的 `.env` 中填写 `GEMINI_API_KEY`。向量索引在 PostgreSQL 里，上传文件和知识库清单保存在 Compose 命名卷中。停止并删除容器：

```bash
docker compose down
```

上述命令默认保留命名卷。如需同时删除本地容器数据，请明确执行 `docker compose down --volumes`。

## API

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| `GET/POST` | `/api/auth/bootstrap` | 查询初始化状态或创建首位管理员；创建仅允许一次 |
| `POST` | `/api/auth/login` | 登录并创建可撤销 Bearer 会话 |
| `POST` | `/api/auth/logout` | 撤销当前会话 |
| `GET` | `/api/auth/me` | 获取当前成员及角色 |
| `GET/POST/PUT` | `/api/members` | 管理员查看、创建或更新成员 |
| `GET` | `/api/knowledge-bases/{id}/members` | 管理员查看知识库成员 |
| `PUT/DELETE` | `/api/knowledge-bases/{id}/members/{user_id}` | 管理员授予或撤销知识库成员 |
| `POST` | `/api/documents` | 上传并索引 MD、TXT 或 PDF |
| `GET` | `/api/documents` | 获取已索引文档及 chunk 数量 |
| `DELETE` | `/api/documents/{id}` | 删除文档、向量与本地上传文件 |
| `POST` | `/api/query` | 检索、精排并生成带来源答案 |
| `GET` | `/api/evaluations` | 按运行时间倒序获取正式检索评测报告 |
| `GET` | `/api/evaluations/{report_id}` | 获取单次正式检索评测报告详情 |
| `GET` | `/api/health` | 检查索引与模型配置状态 |
| `GET` | `/api/health/live` | 存活检查，不初始化重量模型 |
| `GET` | `/api/health/ready` | 检查认证、审计和业务存储是否就绪 |
| `GET` | `/api/system/metrics` | 管理员读取进程内请求、RAG 和索引指标 |
| `GET` | `/api/audit/events` | 管理员分页查询追加式审计事件 |
| `GET/POST` | `/api/knowledge-bases` | 列出或创建知识库 |
| `GET/PUT/DELETE` | `/api/knowledge-bases/{id}` | 查看、更新或删除空知识库 |
| `GET/POST` | `/api/knowledge-bases/{id}/documents` | 列出或上传指定知识库的文档 |
| `DELETE` | `/api/knowledge-bases/{id}/documents/{document_id}` | 删除指定知识库的文档 |
| `POST` | `/api/knowledge-bases/{id}/query` | 只检索指定知识库并生成答案 |
| `GET` | `/api/knowledge-bases/{id}/index-versions` | 管理员查看索引版本、状态与放行报告；切换只提供 CLI 入口 |
| `GET` | `/api/knowledge-bases/{id}/conversations` | 获取指定知识库的会话历史 |
| `GET` | `/api/knowledge-bases/{id}/conversations/{conversation_id}` | 获取会话及回答记录 |
| `DELETE` | `/api/knowledge-bases/{id}/conversations/{conversation_id}` | 删除会话及其回答记录 |
| `GET` | `/api/knowledge-bases/{id}/answers/{record_id}` | 获取单条回答、来源和执行元数据 |

查询请求示例：

```json
{
  "question": "项目如何保证回答可追溯？",
  "retrieve_k": 10,
  "rerank_k": 5
}
```

## 评测

```bash
DATABASE_URL="$TEST_DATABASE_URL" uv run python -m evaluations.evaluate
```

脚本自建隔离知识库、索引 `knowledge/project-profile.md`、计算向量检索的 Recall@10、MRR
和精排后的 MRR，跑完清理。标注锚定的是解析器的段落编号，因此解析与切分留在被测链路里。
仓库只提供可复现的评测方法，不提交未经实际运行的结果数字。

实测结果（4 个标注问题，2026-08-28 在 pgvector 上复跑，与 2026-08-02 的 Chroma 结果逐位相同）：

| 指标 | 结果 |
| --- | ---: |
| 向量召回 Recall@10 | 1.000 |
| 向量排序 MRR | 0.688 |
| CrossEncoder 精排 MRR | 1.000 |

小规模演示集仅用于验证评测链路，不代表通用数据集表现。

V2 固定检索集的正式基线使用真实 embedding、CrossEncoder 和隔离的临时知识库生成。
该数据集的候选分块是写死的，解析与切分不在被测范围，因此分块直接写入 pgvector，
不经过解析链路：

```bash
uv run python -m backend.evaluation.run_baseline \
  --dataset backend/evaluation/datasets/retrieval_v1.json \
  --database-url "$TEST_DATABASE_URL" \
  --commit "$(git rev-parse HEAD)" \
  --output backend/evaluation/reports/retrieval_v1_baseline.json
```

报告记录数据版本、模型 revision、代码提交、运行参数、Recall@5、向量 MRR、精排 MRR
及冻结质量门结论。正式报告必须由真实模型生成，测试替身结果不得写入该目录。

V2 融合排序以 15% 归一化向量分数和 85% 归一化 CrossEncoder 分数计算最终顺序。
固定数据集的正式结果如下，完整运行上下文见
`backend/evaluation/reports/retrieval_v1_optimized.json`：

| 指标 | V2 基线 | 融合排序 | 是否回退 |
| --- | ---: | ---: | --- |
| Recall@5 | 1.000 | 1.000 | 否 |
| 向量 MRR | 0.9083 | 0.9083 | 否 |
| 最终排序 MRR | 0.9667 | 0.9750 | 否 |

复现并与冻结基线比较：

```bash
uv run python -m backend.evaluation.run_baseline \
  --dataset backend/evaluation/datasets/retrieval_v1.json \
  --commit "$(git rev-parse HEAD)" \
  --baseline-report backend/evaluation/reports/retrieval_v1_baseline.json \
  --output backend/evaluation/reports/retrieval_v1_optimized.json
```

### 语料级检索评测（2.0.0）

`retrieval_v1.json` 的候选分块是写死的，解析与切分不在被测范围，因此切分参数变化不会
反映到指标上。`corpus_v2.json` 以 `docs/` 的 10 篇真实中文文档为冻结语料（快照存放在
`backend/evaluation/datasets/corpus_v2/`，按 SHA-256 与解析段落数双重锁定），每次评测
重新执行 `parse_document` 与 `split_sections`，指标按原始段落粒度统计：

```bash
uv run python -m backend.evaluation.run_corpus_baseline \
  --dataset backend/evaluation/datasets/corpus_v2.json \
  --database-url "$TEST_DATABASE_URL" \
  --commit "$(git rev-parse HEAD)" \
  --output backend/evaluation/reports/corpus_v2_baseline.json
```

评测会真实写入 PostgreSQL、由索引 Worker 处理并通过 pgvector 查询。为避免污染业务数据，
命令只接受 schema 3 且不含用户或知识库的隔离数据库；运行结束会清理临时语料。冻结阈值为
Recall@5 `0.70`、向量 MRR `0.55`、精排 MRR `0.65`，不按实测结果倒推。

首份 2.0.0 报告须在评测实现与数据集进入 commit 后生成，报告中的 `commit` 必须正好包含
被测代码和冻结语料。未通过门槛的报告会保留为 `official: false`，不会进入只读评测 API。
1.0.0 数据集与其正式报告保持不变，继续守护融合排序策略的回归。

Schema V10（commit `a588a39`）下的实测结果，145 个标注问题，两份报告分别对应默认切分
与更细切分，运行上下文见 `backend/evaluation/reports/corpus_v2_baseline.json` 与
`corpus_v2_optimized.json`：

| 指标 | 700/100（默认） | 160/20 | 冻结阈值 |
| --- | ---: | ---: | ---: |
| Recall@5（召回阶段） | 0.6862 ❌ | **0.7276** ✅ | 0.70 |
| 向量 MRR | 0.5505 ✅ | 0.5733 ✅ | 0.55 |
| 精排 MRR | 0.7695 ✅ | 0.7721 ✅ | 0.65 |
| 精排后 Recall@5（用户实际看到的来源） | 0.8069 ✅ | 0.8241 ✅ | 0.70 |
| 分块数 | 232 | 286 | — |

**默认切分配置过不了自己的质量门，更细的切分能过。** 700/100 的召回阶段 0.6862 差
`0.0138` 达标，报告保留为 `official: false`；把 chunk_size 降到 160、overlap 降到 20 后
召回升到 0.7276（`+0.0414`），四项全部达标，`official: true`。四项指标同向改善，不是
以某一项换另一项。阈值不按实测结果倒推，不下调。

这与「解析切分的实测收益」一节的结论一致：段落划分决定检索上限，而那节用的是改写集，
这里用原始集在**绝对阈值**上复现了同一方向。默认配置未随之调整——`chunk_size` 的默认值
影响所有既有部署，属于需要单独评估的变更。

`--chunk-size` 与 `--chunk-overlap` 可覆盖切分配置，用于对比不同切分策略的实际收益。
schema 版本以 `REQUIRED_DATABASE_SCHEMA_VERSION` 为准。

### 同义改写评测集与召回诊断

`corpus_v2.json` 的问句是照着原文写的，与原文用词高度重合，会系统性高估词法检索。
`corpus_v2_paraphrased.json` 共用同一份语料与段落标注，只把问法换成口语化表达
（与原问句的平均词元重合率 14%），用于检验结论是否可推广：

```bash
uv run python -m backend.evaluation.run_corpus_baseline \
  --dataset backend/evaluation/datasets/corpus_v2_paraphrased.json \
  --database-url "$TEST_DATABASE_URL" --commit "$(git rev-parse HEAD)" \
  --retrieval-mode vector --output /tmp/para.json
```

**两个数据集难度不同，不共用阈值。** 同一配置下原始集 0.63、改写集 0.44；`0.70` 是为
原始集定的，改写集只用于相对比较。

聚合指标无法区分"语义匹配不上"与"排在第 17 名被 top-5 截断"，诊断工具用更大的窗口
检索并记录每个标注段落的真实名次：

```bash
uv run python -m backend.evaluation.diagnose_retrieval \
  --dataset backend/evaluation/datasets/corpus_v2_paraphrased.json \
  --database-url "$TEST_DATABASE_URL" --retrieval-mode vector --diagnose-k 50
```

指标中 `recall_at_5` 统计的是召回阶段，`rerank_recall_at_5` 才对应用户实际看到的
来源列表——扩大召回窗口的收益只有后者能体现。

### 混合检索（默认关闭）

`retrieval_mode` 支持 `vector`（默认）与 `hybrid`。hybrid 用 RRF 合并向量与 BM25
两路名次：RRF 只看名次不看分数，因此余弦与 BM25 的量纲差异无需归一化即可合并。
词法检索零依赖实现，中文按字符 bigram 切分、ASCII 标识符整体保留，`NodePort`、
`30080`、`FOR UPDATE SKIP LOCKED` 这类词元不会被切碎。

**实测结论：切分修复之后没有开启的理由。** 在改写集上 hybrid 反而落后于纯向量
（精排后 Recall@5 `0.6048` vs `0.6410`）——hybrid 早期的领先来自补偿切分损坏造成的
召回缺口，缺口修复后词法兜底的边际价值消失。代码保留以备语料形态变化时重新评估。

### 解析切分的实测收益

段落划分直接决定检索上限。以改写集与 `BAAI/bge-base-zh-v1.5` 衡量：

| 解析策略 | 召回 Recall@5 | 精排后 Recall@5 | 精排 MRR | 分块数 |
| --- | ---: | ---: | ---: | ---: |
| 原始（按空行切分） | 0.4724 | 0.5345 | 0.4102 | 189 |
| + 代码块并入上下文 | 0.5345 | 0.5759 | 0.4300 | 170 |
| + 长列表按要点拆分 | 0.5428 | **0.6410** | **0.4723** | 232 |

累计精排后 Recall@5 提升 `+0.107`（相对 20%），且第三行是在标注变严格
（3 条问题改为多标注，分母变大）的前提下取得的。两项改动的机理不同：

- **代码块并入上下文**：按空行切分会把 ``` 代码块拦腰截断，并使其独立成段。
  纯命令文本没有任何自然语言描述，"怎么校验备份包"与一段 shell 命令之间没有可
  匹配的语义，任何向量模型都命中不了。
- **长列表按要点拆分**：一个段落塞进五六个要点，只会得到一个被稀释的向量。收益
  几乎全部体现在精排后（`+0.065`）而非召回阶段（`+0.008`）——拆分没让向量多找到
  什么，但候选变纯净后精排器能排得准得多。

**失败的尝试**：给每个段落注入所属小节标题，实测有害（召回 Recall@5 从 `0.5345`
跌到 `0.4586`）——同一小节下的段落因共享标题前缀而向量趋同，区分度被破坏。原因
记录在 `parsers.py` 的注释里，避免重复试错。

### 数据源增量同步

资料不必再逐个手工上传。把一个本地目录或 S3 兼容存储桶登记为数据源之后，同步会自己识别
新增、内容更新与删除，只处理变化的那部分：

```bash
# 1a. 登记本地目录数据源（一个数据源 = 一个根目录）
uv run python -m scripts.sync_data_source create \
  --knowledge-base kb_default --name 手册目录 --root /mnt/enterprise-docs --suffixes .md,.pdf

# 1b. 或登记 S3 兼容对象存储（一个数据源 = 一个桶 + 一个前缀）
export ENTERPRISE_DOCS_ACCESS_KEY=...   # 密钥只走环境变量，见下
export ENTERPRISE_DOCS_SECRET_KEY=...
uv run python -m scripts.sync_data_source create \
  --knowledge-base kb_default --name 手册桶 --type object_storage \
  --endpoint s3.example.com --bucket enterprise-docs --prefix handbook/ \
  --region cn-north-1 --credential-env ENTERPRISE_DOCS

# 2. 发起同步；需要 Worker 在跑才会真正执行
uv run python -m scripts.sync_data_source sync --data-source "$DATA_SOURCE_ID"
uv run python -m scripts.index_worker

# 3. 查看状态与最近一次失败原因
uv run python -m scripts.sync_data_source status --data-source "$DATA_SOURCE_ID"
uv run python -m scripts.sync_data_source list --knowledge-base kb_default
```

**访问密钥绝不进数据库。** 对象存储的密钥只从环境变量 `{--credential-env}_ACCESS_KEY`
与 `_SECRET_KEY` 读取，CLI 刻意不提供任何接收密钥的参数。写进 `configuration` 会让数据库
备份、审计 payload 和只读数据源接口同时变成密钥泄露面。副作用是轮换密钥只改环境变量，
不动数据库。环境变量缺失时同步以 `SOURCE_CREDENTIALS_MISSING` 失败，**不回退匿名访问**
——回退会让配置错误表现成「桶是空的」，而空清单会被判成全部删除。

**变更判定不看修改时间，也不看大小。** mtime 会在同内容重新落盘时改变（rsync、网盘客户端
重传、`cp` 都会），用它做判定会触发大量无谓的重新解析与重新 embedding；而编辑后大小恰好
不变的情况也真实存在，所以 size 同样不可靠。两种数据源的判定依据不同：

- **本地目录**用内容 SHA-256。`touch` 全部文件后再同步会产生零个索引任务。
- **对象存储**用服务端 ETag。契约只保证单向——内容变了 ETag 一定变，反向不保证。

**换分段大小重传会触发该对象重新索引。** 分段上传的 ETag 不是内容 MD5，而是各段 MD5
拼接后再取 MD5 加 `-段数` 后缀，同一份内容换个 `part_size` 上传就得到不同 ETag。运维换上传
工具或调了分段大小，整桶会被判成已更新并重跑一轮索引。这是接受 ETag 作为版本标识的已知
代价：误判方向安全（多做一次索引，结果正确），而致命的那一半——内容变了却没被发现——
不会发生。不选「下载内容自己算哈希」是因为那要求每次同步拉取整个桶，等于把对象存储退化成
远程的本地目录。

**超过 `MAX_UPLOAD_MB`（默认 15）的对象在列举阶段被跳过，不下载。** 它不入队、不软删、
不计入熔断分母，文档列表里也不会出现——它从未被索引过。跳过对同步结果是「成功」，对提问
的人却是「这份资料不在库里」，所以每个被跳过的对象都会在 Worker 日志里留一条
`data_source.object_skipped`，带上 `object_key`、`size_bytes` 与 `max_bytes`：

```bash
uv run python -m scripts.index_worker 2>&1 | grep object_skipped | jq -c '{object_key, size_bytes}'
```

上限与
上传路径共用一个配置项：真正的约束是解析与 embedding 的处理能力，而它对两条路径完全相同，
「对象存储里文件更大」不构成放宽理由。要放宽就整体调高 `MAX_UPLOAD_MB`。不把它当作
「失败」是因为失败会被自动重试，而超限对象每次必然失败，等于每轮都白下载一遍那个大文件。

**删除是软删除。** 对象在数据源里消失后，它的分块不再进检索，但文档记录、版本记录与
向量全部保留（`retrieval_status` 置 `deleted`）。物理删除仍只能由人显式执行。对象重新
出现时自动恢复可检索——代价是会重新解析索引一次，即使内容没变，原因见
`docs/design/v5-6-sync-pipeline.md` 第 7 节。

**索引失败会自动重试。** 某份文档解析或嵌入失败后，后续同步会重新处理它，故障恢复后
自动补齐，不需要人工干预。判定依据是「当前版本是否已 ready」而不是「是否见过这个
version」——否则失败的文档会被永久跳过，而列表里一直显示失败。

**删除熔断**：单次同步的删除量**同时**超过绝对下限（`SYNC_DELETE_MINIMUM`，默认 3）与
比例阈值（`SYNC_DELETE_THRESHOLD_PERCENT`，默认 30）时中止，不执行任何删除也不执行
任何新增，并把待删清单写进失败原因。它挡的是配置错误而非真实删除——根目录被误改、
挂载点掉了、导出任务没跑成功，都会让列举结果几乎为空。两个条件都要满足是因为纯比例
在小知识库上过于敏感：3 份文档删 1 份就是 33%，而那是正常操作。

根目录不存在或存储桶不可访问时，同步以 `SOURCE_ROOT_UNAVAILABLE` 失败，**不会**产出空
清单——空清单会被差异计算判定为「全部删除」，那是把配置错误伪装成数据变更。两种数据源
共用这个错误码是有意的：语义相同，不该各造一套。

已实现范围与边界：

- 实现本地目录（`local_directory`）与 S3 兼容对象存储（`object_storage`）。`web` /
  `connector` 两个 `source_type` 仍是 `0001` 里的预留值，**不对应任何实现**。
- 同步由操作者显式触发，没有定时同步与 webhook。
- 一个本地目录数据源对应一个根目录，一个对象存储数据源对应一个桶加一个前缀；都不支持
  多路径或通配匹配。
- 不感知 S3 对象版本控制（versioning），桶开了也只同步当前版本。
- **未做分批**：单次同步在超大桶上可能长时间占用 Worker（单 Worker 串行），并可能超过
  `INDEX_JOB_STALE_SECONDS`（默认 900）导致租约被回收、滚动更新期间重复领取。
- 本地目录跳过符号链接与隐藏文件。同一数据源同时只允许一个活动同步任务。

### 分类治理：没有分类不是一种分类

资料可以**暂时没有分类**，这在库里就是 `category_id IS NULL`，不是「归到一个叫未分类的
分类里」。V16 之前系统会给每个知识库自动建一个 `is_system` 的「未分类」，于是「模型超时
分类失败」和「管理员就是没给它分类」在库里长得一模一样，谁也没法按状态治理。

**归属与状态是两个独立字段**：

| | 取值 | 含义 |
| --- | --- | --- |
| 分类归属 | `category_id` / `category` | 真实业务分类，或 `null` 表示没有分类 |
| 处理状态 | `classification_status` | `pending`、`auto_assigned`、`review_required`、`manual`、`failed` |

只有**高置信**的自动分类才会写入归属。低置信结果落在 `review_required`，把猜测放进
`suggested_category_id` 供人一键确认，但**不写**分类字段——猜错的分类比没有分类更难发现，
前者看起来一切正常，后者至少在列表里显示为空。

**分类失败有稳定错误码，并据此决定是否自动重试**：

| 错误码 | 自动重试 | 处置 |
| --- | :---: | --- |
| `MODEL_UNAVAILABLE` | 是 | 分类模型未就绪，检查 `GEMINI_API_KEY` 与模型可用性 |
| `MODEL_TIMEOUT` | 是 | 模型超时，通常等一等会自己好 |
| `UNKNOWN_ERROR` | 是 | 未预料的调用异常，真是偶发就自己好了 |
| `INVALID_RESPONSE` | 否 | 模型返回的不是约定 JSON，重试只会再拿到一份不对的格式 |
| `CATEGORY_NOT_FOUND` | 否 | 模型编了一个不存在的分类，看提示词与候选清单 |
| `CATEGORY_INACTIVE` | 否 | 选中的分类已停用，要么启用它，要么人工归类 |
| `NO_ACTIVE_CATEGORY` | 否 | 知识库分类字典是空的，先建分类 |

后四种重试多少次都是同样的结果，它们要的是人去改配置。所以只有可重试的失败才累加
`classification_retry_count`——让不可重试的失败把次数耗光，只会掩盖「这需要人来处理」。

**重试走 `index_jobs` 队列**（`job_type = 'classify'`），退避、最大次数（`INDEX_JOB_MAX_ATTEMPTS`，
默认 3）与租约恢复因此全部复用索引任务的既有机制，不另造一套调度。可重试的失败让任务回到
`queued` 并推迟 5 秒，下次重试时间写进 `classification_next_retry_at`；不可重试的失败原地
记录、任务本身算完成——它已经做完了该做的事，反复重来只会白烧模型调用。

**分类失败不阻断任何东西**。上传、解析、切片、索引都已经完成，分类只是没跑成。资料照常
可检索，只是没有分类。

**检索边界**：不指定分类时，无分类资料**参与**召回——否则一份刚上传、分类还没跑完的资料
就等于从库里消失了。指定 `category_ids` 时，无分类资料**不参与**候选、精排、Prompt 与引用：
用户划定了范围就要守住。名称过滤 `categories` 会先解析成真实分类 ID 再执行，解析不出任何
分类时按「过滤不到」处理而不是放行——放行等于把用户的过滤条件悄悄取消。回答的
`query_metadata.uncategorized_candidate_count` 报告本次召回里有几条没有分类。

**治理操作**都在同一事务里同时更新 Document 与当前活动版本的 Chunk，历史版本保留当时的
快照。重新分类（`POST /api/knowledge-bases/{id}/documents/reclassify`）把状态退回 `pending`、
清空失败信息，**并入队一个分类任务**——需要 Worker 在跑才会真正执行。它只重跑分类，
**不重新解析、切分或 embedding**：分类失败是分类的问题，资料主体已经处理好了，摘要直接取
当前版本已切好的分块，连原文都不必再读一遍。

「未分类」现在是一个**普通分类名**，管理员可以像其他分类一样创建、改名、停用、删除它。
分类删除的唯一门槛是「还有资料引用它」（`CATEGORY_IN_USE`），没有删不掉的特权分类。

### 索引版本切换与回滚

换切分参数会改变检索质量，但改差了必须能退回去。索引版本让新旧两套分块在库中并存：
重建写入一个未放行的 `building` 版本，用户检索完全看不到；验收通过后原子切换读指针，
回滚就是把指针切回上一个版本。

完整流程：

```bash
# 1. 发起重建，得到 index_version_id 与 batch_id
uv run python -m scripts.rebuild_index start \
  --knowledge-base kb_default --chunk-size 160 --chunk-overlap 20

# 2. 跑 Worker 处理重建任务，再查状态（status 会把跑完的批次推进到 ready）
uv run python -m scripts.index_worker
uv run python -m scripts.switch_index status --batch "$BATCH_ID"

# 3. 在隔离评测库上用同一套配置生成放行报告，并以当前 active 版本的报告作基线
uv run python -m backend.evaluation.run_corpus_baseline \
  --dataset backend/evaluation/datasets/corpus_v2.json \
  --database-url "$TEST_DATABASE_URL" --commit "$(git rev-parse HEAD)" \
  --chunk-size 160 --chunk-overlap 20 \
  --baseline-report backend/evaluation/reports/corpus_v2_baseline.json \
  --output /tmp/candidate.json

# 4. 切换（校验不通过就拒绝），必要时回滚
uv run python -m scripts.switch_index switch \
  --index-version "$INDEX_VERSION_ID" --report /tmp/candidate.json
uv run python -m scripts.switch_index rollback --knowledge-base kb_default

# 5. 确认不再需要旧版本后显式清理
uv run python -m scripts.switch_index retire --index-version "$OLD_INDEX_VERSION_ID"
```

切换的三道校验，任一不通过即拒绝：目标版本状态为 `ready`、三项指标**未相对基线回退**、
**报告的配置指纹与索引版本逐位相同**。

质量门是**相对比较**，不要求达到冻结的绝对阈值。两者回答不同问题：绝对阈值（Recall@5
`0.70` 等）回答"这套系统能否上线"，切换要回答的是"这次换配置是变好还是变坏"。回退判定
沿用 `assess_metric` 既有的 `baseline` 与 `max_regression`（默认 0.02）语义，不另造规则——
生成报告时用 `--baseline-report` 指向当前 active 版本的报告即可。绝对阈值结论仍会如实
返回在 `meets_frozen_thresholds` 字段里，切到未达标的索引时操作者看得到。

配置指纹是这里真正的牙齿：报告里的任何布尔字段都可以被伪造，指纹不行——它必须由被测
配置本身算出来，因此能挡住"用 A 配置跑出的合格报告去放行 B 配置的索引"。

不再检查 `official`：它在 `run_corpus_baseline` 里就等于 `passed`，检查它等于又查一遍
绝对阈值。

**质量门的口径边界**：它验证的是"该配置在冻结语料上不回退"，不代表验证了生产数据的
检索质量——生产语料没有段落标注，算不出 Recall。评测入口本身要求隔离空库，本就不能
在生产库上运行。

已知限制：

- 配置指纹不含各格式的 parser 版本，只含全局 `PARSER_SCHEMA_VERSION`。per-format 版本
  由文档格式决定（Markdown 与 PDF 是 `2.0`，DOCX 与 CSV 是 `1.0`），评测语料的格式组合
  与生产知识库必然不同，纳入会让指纹永远匹配不上。
- 不支持跨维度 embedding 模型的并存回滚。`chunks.embedding` 固定维度才能建 pgvector
  索引（无维度列会报 `column does not have dimensions`），固定后装不下两种维度。
- `previous` 版本不会自动过期，一直保留到显式 `retire`；`retired` 状态只表示"可以删除"，
  不代表数据已删。
- **回滚只有一次机会**。回滚会把原 active 降为 `ready` 而不是 `previous`，因此回滚后不再
  存在可回滚目标；要再切回去必须重新走一次质量门（该版本的 `ready` 状态仍在，重新提供
  合格报告即可 `switch`）。这样设计是为了不出现两个 `previous` 而破坏唯一约束。
- `retire` 的删分块与删部分索引不在同一事务（DDL 需要 autocommit）。进程在两步之间中断
  会留下一个 0 行的部分索引，重跑 `retire` 即可清掉。
- Schema V10 迁移在单事务内对存量分块建 HNSW 索引，会锁表，不能用 `CONCURRENTLY`。
- 切换与回滚只有 CLI 入口，只读列表有 API；前端不提供切换按钮。

### V3 回答质量工具链

固定回答评测集位于 `backend/evaluation/datasets/answer_v1.json`，包含 30 个稳定样本：
20 个有充分证据的中文问答，以及 10 个证据不足、来源冲突、检索为空和生成失败场景。
每题记录知识库、参考要点、允许来源、禁止断言、期望状态及失败时是否必须保留来源。

回答运行记录必须包含被测 commit、数据集版本、Prompt 版本/哈希、模型标识、参数和逐题
API 结果。快速模式只执行引用编号与失败结构等确定性检查，不调用模型裁判：

```bash
uv run python -m backend.evaluation.run_answer_evaluation \
  --dataset backend/evaluation/datasets/answer_v1.json \
  --run /path/to/answer-run.json \
  --mode fast \
  --output /tmp/answer-fast-report.json
```

正式候选模式还要求每个可回答样本提供逐声明语义裁判结果，包括正确性、完整性、支持证据、
引用编号、是否有来源支持、是否与来源矛盾及归因是否正确：

```bash
uv run python -m backend.evaluation.run_answer_evaluation \
  --dataset backend/evaluation/datasets/answer_v1.json \
  --run /path/to/answer-run-with-judgements.json \
  --mode formal \
  --output /tmp/answer-formal-candidate.json
```

生成模型不能作为唯一裁判。工具链输出默认都是非正式候选报告；测试替身只验证计算和报告
结构，不能形成正式质量分数。正式基线、人工抽检和放行报告属于 V3 #44。

真实回答基线使用当前配置的 Gemini 生成模型和独立裁判模型逐题运行，并通过检查点支持免费
额度下的断点恢复：

```bash
uv run python -m backend.evaluation.run_answer_baseline \
  --dataset backend/evaluation/datasets/answer_v1.json \
  --corpus backend/evaluation/datasets/retrieval_v1.json \
  --commit "$(git rev-parse HEAD)" \
  --judge-model gemini-3.1-flash-lite \
  --checkpoint /tmp/answer-baseline-checkpoint.json \
  --run-output /tmp/answer-baseline-run.json \
  --report-output /tmp/answer-baseline-report.json
```

运行器每完成一题便原子写入检查点；配额恢复后用相同参数重跑会跳过已完成样本。只有全部
冻结指标通过，并由人工复核全部失败样本及至少 20% 的可回答样本后，候选报告才允许标记
为正式报告。检查点和包含模型原始答案的运行文件默认保留在临时目录，不提交到仓库。

正式回答报告存放在 `backend/evaluation/reports/answers/`，与顶层 V2 检索报告隔离；现有
检索报告 API 不会把回答报告误解析为检索指标。回答报告的只读 API 和页面属于 V3 #45。

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

- `.env` 与 `data/uploads/` 已加入 `.gitignore`；向量索引在 PostgreSQL 里，不落 Git。
- 上传只接受安全文件名及 MD、TXT、PDF 类型，并校验大小、MIME 和基础内容特征；原始
  上传文件以仅当前服务用户可读写的权限落盘。接口响应默认禁止缓存并设置基础安全头。
- 登录和上传/问答等高成本请求设置进程内滑动窗口限流；高成本任务还受并发上限保护。
  这是单实例基础保护，不冒充外部 WAF 或多实例共享限流。
- 参数校验和未预期异常只返回稳定错误码，不回传密码、令牌、请求原文或内部异常文本。
- 每个 API 请求都有 `X-Request-ID`，结构化日志只记录路由模板、状态、耗时和匿名主体；
  不记录查询字符串、请求体或业务正文。管理员可读取请求、RAG 与索引聚合指标。
- 审计事件以哈希链追加保存于 `data/audit/`，覆盖登录、成员/权限、知识库和文档变更；
  只读接口仅管理员可访问。审计记录不包含问题、答案、Prompt、令牌、密钥或文件正文。
- 账号、密码摘要、会话摘要和知识库授权位于 `data/auth/`；密码使用带随机盐的 scrypt
  摘要，会话只保存令牌 SHA-256 摘要，原始密码和原始令牌不会写入存储文件。

备份清单、完整性校验、隔离恢复演练、测试 RPO/RTO 口径、数据保留和故障回滚步骤见
[备份恢复与数据生命周期运行手册](docs/operations/backup-recovery.md)。恢复工具强制使用空目标目录，
不会覆盖现有数据；Render 或其他正式环境恢复仍须另行批准。

PostgreSQL 版本的 Tag 质量门、GHCR 不可变镜像、制品清单和前一版本隔离回滚流程见
[PostgreSQL 版本发布与受控回滚](docs/operations/release-rollback.md)。发布必须由明确批准的 `v5.x.y`
Tag 触发；普通 PR 不创建版本制品，也不会触发 Render 部署。回滚流程使用隔离的 PostgreSQL、
pgvector 和原始文件目标，并以独立 Worker 验证当前生产运行模式。

V4 的生产就绪验收范围、证据索引、已知限制与 V0→V4 总复盘见
[V4 生产就绪验收报告](docs/reports/v4-readiness.md)。版本 commit、镜像摘要和回滚记录以
GitHub Release 附件为准，不在报告中复制维护。
- 管理员可访问全部知识库；普通成员只看到被明确授权的知识库。服务端对未授权知识库统一
  返回“未找到”，避免通过错误差异探测资源是否存在。正式评测和成员管理仅管理员可访问。
- V3 起每个文档和来源都带有 `knowledge_base_id`；V2 数据统一迁入稳定的默认知识库
  `kb_default`，原始文件存放于 `data/uploads/kb_default/`。
- 原 V2 文档和查询 API 继续映射默认知识库；V3 作用域 API 通过路径中的
  `knowledge_base_id` 严格隔离上传文件、向量检索与删除操作。
- 默认知识库不能删除；其他知识库包含文档时也不能删除，必须先明确删除其中的文档。
- 每次有效查询都会保存成功或失败记录，包括来源快照、分段耗时、模型集合、Prompt
  版本/哈希及稳定错误码；只保存 Prompt 哈希，不保存完整 Prompt。
- 会话和回答记录位于 `data/conversations/`，并严格绑定 `knowledge_base_id`；Render
  免费演示环境仍只保证当前实例生命周期内可用，不承诺跨休眠或重新部署保留。
- 演示资料由项目作者编写，不包含真实电话、邮箱或访问令牌。
- 不建议把包含个人敏感信息的索引目录或 API 原始响应公开提交。
- V4 威胁边界、已缓解风险与延期项见
  [`docs/security/v4-threat-model.md`](docs/security/v4-threat-model.md)。

## 独立实现与致谢

本项目是为个人作品集从零设计和实现的工程项目，不宣称原创 RAG 算法。学习过程中参考了[马克的技术工作坊：使用 Python 构建 RAG 系统](https://github.com/MarkTechStation/VideoCode/tree/main/%E4%BD%BF%E7%94%A8Python%E6%9E%84%E5%BB%BARAG%E7%B3%BB%E7%BB%9F/rag)所介绍的通用流程。原仓库未提供开源许可证，因此本项目未复制其代码、README 文案或示例文档，仅在此注明概念学习来源。

## License

本仓库暂未添加开源许可证，默认保留所有权利。如需授权复用，请先联系仓库作者。
