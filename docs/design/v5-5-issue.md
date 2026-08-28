## 目标

在 V5-4 结构化解析与切片治理的基础上，建立全库级索引版本切换与回滚能力。重建产出的新索引与
当前索引并存，切换由固定数据集质量门放行，指标回退时可原子回滚到上一版本。

## 当前基线

- 已有：混合检索（V5-1）、查询理解与多查询召回（V5-2）、元数据过滤与 ACL 治理（V5-3）、
  结构化解析与切片治理（V5-4，Schema V9）、可中断续跑的索引重建（#90 / PR #91）。
- 缺口（均以 `0b7de7a` 代码为准）：
  - 重建是销毁式原地替换，`backend/app/postgres_documents.py` 先
    `DELETE FROM chunks WHERE document_version_id=%s` 再写入新分块，旧向量当场丢失，无回退路径。
  - 重建期间索引是混合态：每个 rebuild job 独立事务提交，读路径只按 `current_version_id`
    取分块，用户此刻的检索结果来自两套切分策略。
  - 表结构装不下并存：`chunks` 的 `UNIQUE (document_version_id, chunk_index)`（`0001`）。
  - PostgreSQL 侧没有任何向量索引，`chunks.embedding` 是无维度 `vector` 列，pgvector 对
    无维度列拒绝建 HNSW/IVFFlat（`ERROR: column does not have dimensions`）。

## 实现范围

- Schema V10：新表 `index_versions`（知识库级，冻结 chunking/parser/embedding 配置与配置指纹），
  `chunks` 增 `index_version_id`，`knowledge_bases` 增 `active_index_version_id` 读指针。
- 状态机：`building → ready → active → previous → retired`，`building → failed`；
  每知识库最多一个 active / building / previous，由 partial unique index 保证。
- 重建改造：入队时创建 building 版本并冻结配置，覆盖知识库全量当前版本；worker 写入
  `index_version_id` 且不再删除旧分块；续跑判定改为按"该索引版本是否已覆盖该文档"。
- 切换与回滚：单事务移动读指针。切换三道校验——目标为 `ready`、三项指标未相对基线回退、
  报告配置指纹与索引版本逐位一致。
- 向量索引：固定 `chunks.embedding` 维度（从 `index_settings.embedding_dimension` 读取，
  不写死数字），每个索引版本建一个部分 HNSW 索引，避开 pgvector 带过滤 ANN 查询的
  post-filter 少返回问题。
- 读路径按 active 版本过滤（`query`、`load_current_chunks`、`chunk_fingerprint`、
  `score_by_ids`、`list_documents` 的 chunk_count 共 5 处）；`chunk_fingerprint` 计入 active
  版本 id，使 BM25 倒排在切换后失效重建。
- ACL 与分类元数据的写扩散收窄为只覆盖 active、previous、building 三种状态的分块。
- 评测报告增可选 `config_fingerprint`；缺该字段的报告不能用于放行切换。
- 操作入口：`scripts/switch_index.py`（list / status / switch / rollback / retire）与只读
  `GET /api/knowledge-bases/{id}/index-versions`（仅管理员）。

## 质量门口径

切换的质量门是**相对比较**，不要求达到冻结的绝对阈值。两者回答不同问题：绝对阈值
（Recall@5 `0.70` 等）回答"这套系统能否上线"，切换要回答的是"这次换配置是变好还是变坏"。
回退判定沿用 `assess_metric` 既有的 `baseline` 与 `max_regression`（默认 0.02）语义，
绝对阈值结论如实返回在 `meets_frozen_thresholds` 字段。

不检查 `report.official`：它在 `run_corpus_baseline` 里就等于 `passed`，检查它等于又查一遍
绝对阈值。配置指纹才是真正的牙齿——报告里的布尔字段可以伪造，指纹必须由被测配置本身算出来。

质量门验证的是"该配置在冻结语料上不回退"，**不代表验证了生产数据的检索质量**：生产语料
没有段落标注，算不出 Recall，评测入口本身也要求隔离空库。

## 验收证据

- Schema V10 迁移可应用且幂等，`required_database_schema_version` 与 Compose 配置同步为 10。
- 后端 274 项测试通过，Ruff 通过；前端 20 项测试、ESLint、生产构建通过。
- 真实模型端到端演练（`shibing624/text2vec-base-chinese`）：700/100 索引 → 重建到 160/20 →
  用真实评测报告放行切换 → 回滚，可见分块 38 → 45 → 38，审计链记录
  `index_version.activate` 与 `index_version.rollback` 两条事件并通过完整性校验。
- 重建期间读路径完全看不到 building 版本分块，混合态缺陷已修复。
- 拒绝路径已验证：配置指纹不匹配、报告缺指纹、指标相对基线回退、清理 active 版本。
- V10 语料基线（145 个标注问题，真实 embedding 与 CrossEncoder）：

  | 指标 | 700/100（默认） | 160/20 | 冻结阈值 |
  | --- | ---: | ---: | ---: |
  | Recall@5（召回） | 0.6862 | 0.7276 | 0.70 |
  | 向量 MRR | 0.5505 | 0.5733 | 0.55 |
  | 精排 MRR | 0.7695 | 0.7721 | 0.65 |
  | 精排后 Recall@5 | 0.8069 | 0.8241 | 0.70 |

  默认切分配置过不了绝对阈值，更细的切分能过，四项同向改善。默认 `chunk_size` 未随之调整——
  它影响所有既有部署，属于需要单独评估的变更。

## 阶段边界

- 不支持跨维度 embedding 模型的并存与回滚：固定列维度是换取可用 ANN 索引的代价。
- 不移除 Chroma 运行时，该清理属于独立 Issue。
- 不实施影子检索比对与真实流量采样放行。
- 不实施多数据源同步 Pipeline（归 V5-6）。
- 不实现索引版本管理前端页面：切换属高风险操作，需要单独一轮交互设计。
- 不做自动过期清理：`retired` 只表示"可以删除"，实际删除由 `retire` 显式执行。
- 回滚只有一次机会：回滚把原 active 降为 `ready` 而非 `previous`，再要切回需重新走质量门。
- 迁移在单事务内执行，不使用 `CREATE INDEX CONCURRENTLY`；对存量分块建索引会锁表。

## 遗留问题

- `deploy/kubernetes/configmap.yaml` 的 `REQUIRED_DATABASE_SCHEMA_VERSION` 为 `"5"`、
  `workloads.yaml` 两处为 `"4"`，在 V5-4 时就已落后于实际 schema，本阶段未改动，待确认。
- `PostgresCategoryRepository.update`（分类改名）的分块元数据扩散没有版本条件，
  会刷到已废弃版本的分块，不在本阶段范围。
- 本机 Node 需 `^20.19.0 || >=22.12.0`：低于该下限时 npm 静默跳过 rolldown 的平台原生
  binding，前端测试与构建报 `Cannot find native binding` 且退出码仍为 0。

## 工程事实

实施状态只以子 Issue、PR、commit、CI 为准，本 Issue 不复制维护代码状态。

Refs #92
