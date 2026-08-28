# V5-5 设计：全库级索引版本切换与回滚

日期：2026-08-27
基线 commit：`0b7de7a`（Schema V9，V5-4 已合入 main）
上游归属：[#92 V5 总控](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/92)；范围由
[#98 阶段边界](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/98)（"不实施索引版本切换与回滚，该能力进入 V5-5"）界定。

## 1. 问题

当前索引重建能跑完、能续跑，但不能回退，且重建期间用户看到的是混合索引。三条依据：

| 缺陷 | 出处 |
| --- | --- |
| 重建销毁式原地替换，旧向量当场丢失 | `backend/app/postgres_documents.py:871-873` 先 `DELETE FROM chunks WHERE document_version_id=%s` 再插入新分块 |
| 重建期间索引是混合态 | 每个 rebuild job 独立事务提交；读路径只按 `current_version_id` 取分块，不区分切分版本（`postgres_documents.py:101/147/185`） |
| 表结构装不下新旧并存 | `chunks` 的 `UNIQUE (document_version_id, chunk_index)`（`backend/migrations/0001_postgres_foundation.sql`） |

因此换切分参数或解析策略后指标变差时，没有任何回退路径；`chunking_inventory`
（`postgres_documents.py:687`）只能事后统计已经发生的混合状态。

## 2. pgvector 约束（决定方案形状）

两项外部事实经查证，直接约束设计：

1. **无维度的 `vector` 列建不了 ANN 索引。** pgvector 索引构建对 `dimensions < 0`
   报 `ERROR: column does not have dimensions`，只有带维度修饰的列（如 `vector(768)`）可建
   HNSW/IVFFlat。本项目 `0001` 建表为 `embedding vector NOT NULL`，无维度——**PostgreSQL 侧
   至今没有向量索引不是遗漏，是被这条约束挡住**，`0005_index_embedding_guard.sql` 的注释
   ("chunks.embedding 是无维度约束的 vector") 亦印证。
   来源：[pgvector `hnswbuild.c`](https://github.com/pgvector/pgvector/blob/master/src/hnswbuild.c)
2. **HNSW 叠加 WHERE 过滤是 post-filter，会静默少返回。** 默认 `hnsw.ef_search = 40`，
   索引先取候选再过滤，过滤掉大半即返回远少于 LIMIT 的行，极端情况返回 0 行。
   pgvector 0.8.0 引入 `hnsw.iterative_scan` 缓解；官方对"过滤列只有少数取值"的场景
   推荐**部分索引**（partial index）。
   来源：[pgvector Filtering 文档](https://docs.pgedge.com/pgvector/v0-8-1/filtering/)、
   [0.8.0 iterative scan](https://www.thenile.dev/blog/pgvector-080)

第 2 条对本设计有利：`index_version_id` 天生只有极少取值（同一 KB 同时最多 active、
building、previous 三个），每个索引版本建一个部分 HNSW 索引正好命中官方推荐路径——
查询 WHERE 与索引 WHERE 一致时索引内只含该版本的行，post-filter 问题不出现，
"新旧并存导致延迟翻倍"也随之消除。

第 1 条留下的取舍已决策：**固定 `chunks.embedding` 维度，换取可用的 ANN 索引**。
代价是不同维度的 embedding 模型无法并存回滚，该能力不在本阶段（见第 10 节）。

## 3. 目标与非目标

**目标**

- 全库级（知识库粒度）索引版本切换：整个 KB 重建为一个新索引版本，原子切换读指针。
- 回滚：切回上一个索引版本，其分块在保留期内完整可用。
- 切换放行由固定数据集质量门把关，指标回退即拒绝切换。
- 重建期间用户检索完全看不到未放行的索引版本（顺带修掉第 1 节的混合态缺陷）。
- pgvector 部分 HNSW 索引，使并存不以检索延迟为代价。

**非目标**（本阶段明确不做）

- 跨维度 embedding 模型的并存与回滚。
- 移除 Chroma 运行时（独立 Issue，排在本阶段之后）。
- 影子检索比对与真实流量采样放行。
- 多数据源同步 Pipeline（归 V5-6）。
- 索引版本管理前端页面。
- `CREATE INDEX CONCURRENTLY` 与在线 DDL。

## 4. 数据模型（Schema V10 = `0010_index_versions.sql`）

新表 `index_versions`：

| 列 | 说明 |
| --- | --- |
| `index_version_id` | 主键，`iv_<hex16>` |
| `knowledge_base_id` | 外键，KB 级 |
| `status` | `building` / `ready` / `active` / `previous` / `retired` / `failed` |
| `chunking_version` | 复用 `backend/app/chunking.py` 的 `chunking_version()` |
| `parser_version` | 冻结重建时的解析器版本 |
| `embedding_model` | 冻结重建时的向量模型 |
| `embedding_dimension` | 冻结重建时的向量维度 |
| `processing_options` | `jsonb`，冻结 `chunk_size`、`chunk_overlap` 等切分参数，与 `document_versions` 的同名列同构（`0009`） |
| `config_fingerprint` | `chunking_version`、全局 `PARSER_SCHEMA_VERSION`、`embedding_model`、`embedding_dimension` 与 `processing_options` 规范化 JSON 的 SHA-256。**不含各格式的 parser 版本**：那由文档格式决定（Markdown 与 PDF 是 `2.0`，DOCX 与 CSV 是 `1.0`，见 `parsers.py`），评测语料的格式组合与生产知识库必然不同，纳入它会让指纹永远匹配不上、切换永远被拒 |
| `evaluation_report_id` | 放行依据；转入 `active` 时必填 |
| `rebuild_batch_id` | 关联 `index_jobs.rebuild_batch_id` |
| `created_at` / `activated_at` / `retired_at` | 时间戳 |

其余变更：

- `knowledge_bases` 增 `active_index_version_id`（外键，可空）。
- `chunks` 增 `index_version_id text NOT NULL`（外键）；`UNIQUE (document_version_id, chunk_index)`
  改为 `UNIQUE (document_version_id, index_version_id, chunk_index)`。
- 每个 KB 最多一个 `active`、一个 `building`、一个 `previous`，由三条 partial unique index
  保证，不依赖应用层自律。
- `0005` 的 singleton `index_settings` 保留，但不再是模型/维度的唯一事实源：
  校验移到目标索引版本上，`register_embedding_model`（`postgres_documents.py:557`）
  改为校验"当前进程配置与目标索引版本一致"。它仍是最后一道拦截，不是废弃兼容层。

**存量回填**：为每个 KB 创建一条 `active` 索引版本，`chunks` 全量回填其 id。
配置字段取自 `document_versions.chunking_version` / `parser_version` 与 `index_settings`；
取不到时写 `legacy` 并在该行标注，不猜测历史值。

**维度固定**：`0010` 用 `DO $$ ... EXECUTE format('ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(%s)', dim)`
从 `index_settings.embedding_dimension` 读取实际维度，不写死数字——当前维度由运行时
`len(embeddings[0])` 决定（`postgres_documents.py:865`）。空库时 `index_settings` 无行，
列保持无维度，由首次 `register_embedding_model` 补做 ALTER 并建索引。

**已知限制**：迁移在单事务内执行（`backend/app/database.py:44`），因此 `0010` 对存量分块
建 HNSW 索引会锁表，不能用 `CONCURRENTLY`。当前数据规模下接受该代价。

## 5. 状态机

```text
building --(全部 job succeeded 且覆盖全量文档)--> ready
ready    --(质量门通过 + switch)--------------> active
active   --(被新版本取代)--------------------> previous
previous --(保留期到期或显式 retire)---------> retired
building --(任一 job 终态失败)---------------> failed
```

`previous` 必须保留完整分块，否则回滚不成立。

**状态与删除动作分离**，避免混淆：`retired` 与 `failed` 只表示"分块**可以**被删除"，
并不删除任何数据。实际删除只由 `switch_index.py retire` 显式执行。本阶段不做自动过期——
定时清理需要引入新的调度组件，不属于最小范围；磁盘占用由操作者按运行手册判断。

因此切换时原 `previous` 转为 `retired`（partial unique 约束只允许一个 `previous`），
其分块仍在库中，直到操作者显式清理。

## 6. 重建流程

`enqueue_rebuild` 改造：先创建 `building` 索引版本并冻结全套配置，再为该 KB **全部**
`current_version_id` 入队 rebuild job。不再沿用"只挑 `chunking_version` 与目标不同的版本"
（原先的 `IS DISTINCT FROM`）——全库级切换要求新索引覆盖全量文档，漏一篇即新索引不完整。

**续跑判定随之改变**：原先靠"文档版本的切分配置是否已是目标"跳过已完成的文档，现在
全量入队后这个条件恒为真。改为按"目标索引版本是否已覆盖该文档"跳过：
`NOT EXISTS (SELECT 1 FROM chunks WHERE document_version_id = v.document_version_id
AND index_version_id = <building>)`。重复调用因此仍然安全，中断后再次调用即续跑。

**并发与空库两个边界**：
- 同一知识库只允许一个 `building` 版本。重复调用同一目标配置复用它继续补齐；
  目标配置不同则抛 `REBUILD_IN_PROGRESS`，不产生两套配置混合的半成品索引。
- 没有任何可重建文档时（空知识库，或首次索引尚未完成）直接返回 `queued: 0` 且
  **不创建索引版本**：建出来的会是一个永远无法覆盖全量的空壳，且此时 `index_settings`
  还没有向量模型记录。

`IndexWorker._process`（`:760`）两处改动：

- 写入分块时带 `index_version_id`；**删除 `:871` 的 `DELETE FROM chunks`**。旧版本分块原样保留，
  这是本阶段修掉的核心缺陷。
- rebuild 分支不再回写 `document_versions.chunking_version` 等字段（`:894`），这些事实
  归索引版本管理；`document_versions` 只保留解析结果本身。

**批次完成判定**：该 batch 全部 job `succeeded`，且新版本分块覆盖的文档数等于该 KB 中
`current_version_id` 非空的文档数（尚未成功索引的 pending / failed 文档本就没有可用分块，
不计入分母），才置 `ready`。覆盖数为 0 时判 `failed`——分子分母同时为 0 时"覆盖完整"在
算术上成立，但切过去等于把知识库变成空索引。

该判定挂在 `rebuild_status` 上执行：状态查询是操作者唯一会反复执行的命令，把状态机推进
放在这里，避免"任务都跑完了但版本还停在 building、需要额外命令"的中间态。

**`chunking_inventory` 的统计源随之改变**：从 `document_versions.chunking_version` 换成
索引版本。重建不再回写文档版本的切分配置（新分块此时还在未放行的 building 版本里，
回写会让文档版本谎称自己已是新配置），因此原统计源在重建后不再变化。新语义是
"各索引版本的切分配置 → 覆盖文档数"，并存期间会同时列出新旧两套，正是需要看到的信息。

**失败路径**：任一 job 终态 `failed` 则索引版本置 `failed`，其分块可立即清理，active 指针
不动。现有"重建失败时文档保持可检索"的行为因此自然成立。

## 7. 切换、回滚与质量门

`switch` 接受 `--report <path>` 指向一份评测报告文件，前置校验任一不通过即拒绝：

1. 目标索引版本状态为 `ready`。
2. Recall@5、向量 MRR、精排 MRR 三项均**未相对基线回退**（`regressed` 为假）。
3. 报告记录的配置指纹与目标索引版本的 `config_fingerprint` 逐位相同。

第 3 条是质量门的实际牙齿：报告里的任何布尔字段都可以被伪造，配置指纹不行——它必须由
被测配置本身算出来，因此能阻止用 A 配置跑出的报告去放行 B 配置的索引。

**质量门是相对比较，不是绝对阈值。** 两者回答不同问题：冻结阈值（Recall@5 `0.70` 等）
回答"这套系统能否上线"，而切换要回答的是"这次换配置是变好还是变坏"。因此不检查
`report.passed`，只检查 `regressed`；也不检查 `report.official`——它在
`run_corpus_baseline.py:198` 里就等于 `report.passed`，检查它等于又查一遍绝对阈值。
绝对阈值结论仍在返回值的 `meets_frozen_thresholds` 字段里如实给出。

**复用既有防回退语义，不另造规则。** `assess_metric`（`backend/evaluation/report.py:22`）
已实现 `baseline` 对比与 `max_regression`（默认 0.02）判定。生成放行报告时用
`--baseline-report` 指向当前 active 版本的报告即可，切换命令只读 `regressed`。

**实测数据（V10、145 个标注问题）**：默认切分 700/100 的召回阶段 0.6862 未达 `0.70`
（`official: false`）；改到 160/20 后升至 0.7276，四项全部达标（`official: true`）。
两种配置都能通过相对比较的质量门，后者也能通过绝对阈值。

**报告结构需扩展。** 现有 `RetrievalEvaluationReport`（`report.py:37`）没有配置指纹字段，
只有 `models` 与 `parameters`。需新增可选 `config_fingerprint: str | None`，沿用该文件
处理 `rerank_recall_at_5` 的既有模式（`report.py:56` 注释：旧报告缺该项时保持可选以免失效），
并由 `run_corpus_baseline` 在生成报告时计算写入。**没有 `config_fingerprint` 的报告不能用于
放行切换**——包括 `backend/evaluation/reports/` 下现存的 1.0.0 报告，它们继续承担融合排序
回归职责，不参与索引切换放行。

切换在单个事务内完成：原 `active → previous`、原 `previous → retired`、
目标 `→ active`、更新 `knowledge_bases.active_index_version_id`。
回滚是同一操作的反向执行。两者都写入 `backend/app/audit.py` 的哈希链审计。

**质量门口径边界**（不得在文档或页面上放大）：质量门评的是"这套配置在冻结语料上不回退"。
执行方式是用目标索引版本的配置参数在**隔离评测库**上运行 `run_corpus_baseline`——
该入口本身要求空评测库（`backend/evaluation/run_corpus_baseline.py:71`
的 `_require_empty_evaluation_database`），本就不能在生产库上运行。生产语料没有段落标注，
无法计算 Recall。因此不能表述为"质量门验证了生产数据的检索质量"。

## 8. 读路径、写扩散与词法索引指纹

分块相关的 SQL 分两类处理，**不能一律加 active 过滤**。

### 8.1 读路径：加 active 过滤

以下 SQL 增加 `AND c.index_version_id = <active>`：

| 位置 | 用途 |
| --- | --- |
| `postgres_documents.py:101` | `query`，向量召回 |
| `postgres_documents.py:147` | `load_current_chunks`，词法索引构建与候选复原 |
| `postgres_documents.py:185` | `chunk_fingerprint` |
| `postgres_documents.py:229` | `list_documents` 的 `count(c.chunk_id) AS chunk_count`；不过滤则并存期间文档分块数翻倍 |
| `postgres_documents.py:191` | `score_by_ids`，按 `chunk_id` 精确取值，加过滤是为口径一致 |

active id 在单次请求内查询一次并复用，不在每条 SQL 内重查。

### 8.2 写扩散：必须覆盖所有非 retired 版本

以下两处是 `UPDATE chunks SET metadata=...`，把 ACL 与分类变更刷进分块：

| 位置 | 用途 |
| --- | --- |
| `postgres_repositories.py:295` | data_source ACL 变更同步到分块 |
| `postgres_repositories.py:473` | 分类元数据变更同步到分块 |

这两处**不加 active 过滤**，改为覆盖 `active`、`previous`、`building` 三种状态的分块。

**这里要纠正一处事实**：这两条语句原本的 WHERE 只有 `c.document_version_id = d.current_version_id`
（文档版本，不是索引版本），完全没有索引版本条件。因此 V10 之后它们的默认行为是刷到
**所有**索引版本的分块，包括 `retired` 与 `failed`。本次改动的净效果是**收窄**——排除已废弃
版本，而不是修补一个现存的越权漏洞。

即便如此这个约束必须写下来，因为它约束的是后续实现者：**不许把条件收成
`= active_index_version_id`**。若 ACL 收紧只更新 active 版本，回滚到 `previous` 之后旧版本
分块仍带着收紧前的宽松 ACL，检索会返回本应被拒的内容；`building` 版本同样必须同步，
否则它被切为 active 的瞬间 ACL 就是过期的。

第三处 `postgres_repositories.py` 的分类改名扩散（`PostgresCategoryRepository.update`）
没有 `current_version_id` 条件，本阶段未改动，它仍会刷到已废弃版本的分块。

### 8.3 词法索引指纹

`chunk_fingerprint`（`:168-189`）必须把 `active_index_version_id` 计入返回值。
`LexicalIndexCache`（`backend/app/lexical.py:107`）靠该指纹跨进程判断倒排是否过期；
不做这一步，切换后 API 进程仍使用旧索引版本的 BM25 倒排，混合检索会命中已被切走的分块。
该修复必须与切换实现同一 PR 合入。

## 9. 操作接口

- CLI：`scripts/switch_index.py`，子命令 `list | status | switch | rollback | retire`。
  没有 `prepare`——重建的发起由既有的 `scripts/rebuild_index.py start` 承担，不造第二个入口；
  它的返回值已带上 `index_version_id`。
- 只读 API：`GET /api/knowledge-bases/{id}/index-versions`，仅管理员，返回版本列表、状态、
  配置与放行报告 id。
- 前端本阶段不实现。AGENTS.md 要求"前端只展示后端真实可用能力"，且未授权系统运维页面；
  切换属高风险操作，需要单独一轮设计二次确认交互。

## 10. 测试

| 文件 | 覆盖 |
| --- | --- |
| `backend/tests/test_index_versions.py`（新增） | 三条 partial unique 约束；切换与回滚事务原子性；配置指纹不匹配被拒；指标回退被拒；`previous` 分块未被删除 |
| `backend/tests/test_index_rebuild.py`（扩） | 重建期间读路径看不到 `building` 版本分块；job 部分失败时索引版本进 `failed` 且 active 不动 |
| `backend/tests/test_hybrid_retrieval.py`（扩） | 切换后 `chunk_fingerprint` 变化，BM25 倒排随之重建 |
| `backend/tests/test_postgres_foundation.py`（扩） | V10 迁移幂等；维度 ALTER；存量回填；`REQUIRED_DATABASE_SCHEMA_VERSION` 断言改为 10 |
| `backend/tests/test_corpus_evaluation.py`（扩） | 报告写入 `config_fingerprint`；缺该字段的旧报告仍能反序列化，但不被切换接受 |
| `backend/tests/test_retrieval_access.py`（扩） | ACL 收紧后回滚到 `previous`，被拒用户仍然检索不到——覆盖第 8.2 节的越权风险 |

PostgreSQL 相关测试沿用既有模式：`@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"))`
加 `DROP SCHEMA public CASCADE` 重建（见 `test_index_rebuild.py:50-63`、`:92`）。

**对既有测试的已知影响**：`test_index_rebuild.py:232` 用一个返回 4 维向量的假 embedder 验证
维度冲突被拦截，而现有假 embedder 是 3 维。列固定维度后，冲突会先由 PostgreSQL 的类型
约束报错，而非 `register_embedding_model` 的比较逻辑。该测试的断言需要相应调整，
拦截语义保持不变。
| pgvector 集成测试 | `EXPLAIN` 确认部分索引被使用，且带过滤时不少返回 |

## 11. 验收

- Schema V10 迁移可应用、幂等，`required_database_schema_version` 同步改为 10。
- 一次完整演练：建立新索引版本 → 重建全量 → 隔离评测通过 → 切换 → 检索命中新版本 →
  回滚 → 检索命中旧版本，全过程有审计记录。
- 指标回退与配置指纹不匹配两种情况均能拒绝切换，并留下失败原因。
- 后端测试、Ruff、前端测试与构建、容器质量门全部通过。
- 文档更新 README 检索章节与本文件的已知限制，不宣称未实现的能力。
