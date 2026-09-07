# P0A 只读盘点：现状分层映射与实施计划

日期：2026-09-05
基线 commit：`8052a0f`（Schema V25）
依据规格：`index-m.md` §19「第一步只做只读盘点」
本文档不含任何代码、数据库或配置改动。

## 0. 盘点结论

现状不是「缺少分层」，而是**三代抽象叠加，每一代都没有夺走上一代的职责**：

| 代 | 引入 | 意图承担的层 | 实际承担的 |
| --- | --- | --- | --- |
| V5-4 | `index_jobs` | 索引执行队列 | 仍是索引与同步的**唯一**真实调度器 |
| V5-6 | `sync_runs` + `data_source_objects` | 同步执行记录 | 同步的真实状态源 |
| V5-0024 | `operations` + `index_definitions` + `index_builds` | 统一任务层 + 配置层 | **事后聚合与事后派生**，不持有写权 |

两次「加一层而不迁移职责」的直接后果，是规格 §17 前三条验收全部不成立。

---

## 1. 现有数据同步 → 目标 Data Sync Pipeline

| 目标层 / 实体 | 现状载体 | 差距判定 |
| --- | --- | --- |
| Data Source | `data_sources` 表 | **可复用**。缺 `credential_reference`、`sync_policy`、`last_successful_watermark` 三个规格字段；凭证现状见 §4-C |
| Sync Run | `sync_runs` 表 | **可复用**。`input_cursor` / `discovered_cursor` / `committed_cursor` 已在 V25 落地，且 `pipeline_governance.py:123-124` 只在「全部成功且 `dead_letter_count = 0`」时才推进 `committed_cursor`——符合规格「cursor 只在变更可靠持久化后推进」 |
| Cursor / Watermark | `sync_runs.*_cursor` | **语义不足**。cursor 值是「全部对象 `key:version` 排序后的 SHA-256」（`data_source_sync.py:489-494`），只能回答「远端清单整体是否变了」，**不能**用于增量断点续传或分区同步。规格 §5「分区同步必须保证 cursor 不冲突」在此实现下无法满足 |
| Document | `documents` 表 | **语义偏差**。规格要求 `UNIQUE(data_source_id, source_document_key)`；现状是 `UNIQUE(knowledge_base_id, data_source_id, filename)`（`0001:63`），身份键是文件名而非源侧稳定 key |
| Document Revision | `document_versions` 表 | **部分可复用**。已有 `version_number`、`content_sha256`、不可变约束；**缺** `source_uri`、`source_etag`、`source_modified_at`、`sync_run_id` 四个来源追踪字段 |
| Change Set | 无对应表 | **缺失**。最接近的是 `sync_resource_runs`（逐资源一行），但它是执行明细，不是「一次同步产生的稳定事实集合」。`run_sync()` 的返回值 dict（`data_source_sync.py:695-703`）是唯一的集合级结果，且**不落库** |
| Tombstone | 无对应表 | **缺失**。现状软删除 = `documents.metadata.retrieval_status='deleted'` + 从 `data_source_objects` **删除**该行（`data_source_sync.py:650-651`）。删除记录本身不可查，历史 Snapshot 无从复现 |

### 1.1 同步流程的实际分层状况

`run_sync()`（`data_source_sync.py:452-730`，**279 行单函数**）在一次调用内同时维护 5 处状态：

`data_sources.last_sync_status` / `sync_runs.status` / `sync_resource_runs.status` / `operations.status` / `data_source_objects`

这 5 处分属**至少 10 个独立的 `psycopg.connect`**（该文件共 18 处），**没有共同事务**。任意崩溃点都会留下跨表不一致状态，且现有测试无法覆盖——因为不一致只在特定中断点出现。

进度百分比是硬编码魔数直接嵌在业务流程里：`progress_percent=10`（`:539`）、`10 + 25 * item_index / len` （`:623`）。

---

## 2. 现有索引治理 → 目标 Index Governance Pipeline

| 目标实体 | 现状载体 | 差距判定 |
| --- | --- | --- |
| Index Definition | `index_definitions` 表 | **是装饰层，不是配置源**。`pipeline_governance.py:218-241` 从 `index_versions` **反向派生**；`vector_config` / `keyword_config` / `metadata_schema` / `reranker_config` 四个字段全部是写死的字面量，无任何入口可改。真正驱动构建的仍是 `index_versions.config_fingerprint` |
| Index Version | `index_versions` 表 | **核心可复用**。配置快照、指纹、`building/ready/active/previous/retired/failed` 状态、partial unique 并发保护均已就绪。**缺** `document_snapshot_id`、`definition_id`、`version_no`、`legacy_migrated`、`config_completeness`；缺 `cleaned` 状态 |
| Index Build | `index_builds` 表 | **1:1 而非 1:N**。无 `attempt_no` 列（全仓无此标识符）；`ensure_index_build`（`pipeline_governance.py:249-254`）查到同 `index_version_id` 的既有行就直接返回，一个 Version **永远只能有一次 Build**。规格 §3.8「重试必须新增 attempt」在此结构下无法实现 |
| Validation Report | 无对应表 | **缺失**。放行门禁现状在 `index_versions.switch_to_version()`（`index_versions.py:319-436`），接收的是**内存中的** `RetrievalEvaluationReport` 对象，不落治理库。规格要求的三层门禁只实现了第三层（Retrieval Quality 的 3 个指标 + 指纹比对），**Integrity 与 Technical 两层完全没有** |
| Document Index State | `document_index_states` 表 | **三条 lane 是假的**。`pipeline_governance.py:293-294` 与 `:276` 都用**同一个** `lane` 变量赋给 `vector_status` / `keyword_status` / `metadata_status`，三列恒等。规格要求的 `parse_status`、`chunk_status`、`embedding_status` 三列不存在 |
| Document Snapshot | 无对应表 | **缺失**。见 §5 |
| Lifecycle Events | 无对应表 | **缺失**。现状用通用 `audit` 记录（`index_versions.py:420-428`），且 `actor_id=None` 硬编码——规格 §6.10 要求记录真实 actor |
| Runtime Version Resolver | `PostgresVectorStore._active_index_version()` | **不满足规格 §9**。无缓存、无请求上下文，每次调用独立开连接查库。单次检索链路中被调用 5 处（`postgres_documents.py:79 / 166 / 218 / 259 / 292`）。检索途中发生 Activate 时，同一请求的向量检索与后续过滤**会读到不同版本** |

### 2.1 Activate / Rollback 现状评估

`switch_to_version()`（`index_versions.py:346-419`）已经是**单事务内原子切换**，且注释记录了三条 UPDATE 顺序不可调整的实测理由（`one_previous_idx` 是非延迟 partial unique index）。这部分**质量高于规格基线要求**，P0A 应在其上扩展而非重写。

缺口：
- 未校验 Vector / Keyword / Metadata 组件版本一致性（规格 §6.4）
- 未绑定持久化的 Validation Report（规格 §6.3）
- 无 `retired → cleaned` 状态；`retire_version()`（`:490-514`）直接删分块，版本状态不变

---

## 3. 状态枚举落空清单

`sync_resource_runs` 的 CHECK 声明 14 个 status，代码实际写入 **8 个**。永不出现的 6 个：
`discovered`（仅默认值）、`parsing`、`chunking`、`enriching`、`validating`、`activated`

根因：`index_document()` 把 parse / chunk / embed 一口气做完，同步侧观察不到中间态。

前端连带受影响——`PipelineStepper.tsx:39-47` 为 sync 画了 9 个格子：

- `parse` / `chunk` / `enrich` / `validate` / `activate` 五格后端**永不写入**，恒灰
- 后端实际写入的 `size_limit`、`retry_wait`、`fetch_or_normalize` 三个 stage，前端**没有任何 alias 接得住**
- `vector` / `keyword` / `metadata` 三格因 §2 的假 lane 而永远同亮同灭

---

## 4. 当前耦合点（规格 §18.1.2 要求项）

### A. 模块依赖成环

`pipeline_governance.py` 顶层**不 import 任何本地模块**，全靠函数体内延迟导入绕环。实测三个环：

```
index_versions.py:185      ⇄  pipeline_governance.py:358
postgres_documents.py:867/937/1501/1508  ⇄  pipeline_governance.py:358
postgres_documents.py:1206 ⇄  data_source_sync.py:522
```

全仓 12 处函数体内延迟 import，只有 1 处（`index_versions.py:27` 的 `TYPE_CHECKING`）有正当理由并写了注释，其余 11 处均为规避循环依赖。

### B. 无仓储层，事务边界散落

`psycopg.connect` 分布：`postgres_repositories.py` 45、`postgres_documents.py` 33、`data_source_sync.py` 18、`pipeline_governance.py` 14、`index_versions.py` 11。共 136 处，每个函数自开连接自管事务，没有 UnitOfWork。规格 §14「Repository 只负责持久化」与 §18.2.5「不得绕过领域服务写状态」目前无结构可依托。

`main.py`（2833 行）本身是干净的——不含任何 `psycopg.connect`，只调模块函数。耦合在其下层，不在路由层。

### C. 凭证处理

`_read_credentials()`（`data_source_sync.py:193`）从 `configuration` 读取。规格 §3.1 要求凭证值不得落入普通配置。**本次盘点未验证其实际存储形态**，P0A 启动前需单独确认，不在此下结论。

### D. Sync 直接驱动索引

`run_sync()` 内 `service.index_document(...)`（`data_source_sync.py:590`）直接触发索引链路。规格 §18.2.10 要求「Data Sync 不得直接更新 Version 生命周期」——现状虽未直接改 active 指针，但通过 `active_or_bootstrap_version()`（`postgres_documents.py:1311`）**会在首次索引时创建并直接激活版本**，这是同步路径对索引生命周期的隐式写入。

---

## 5. Document Snapshot 接入点

规格 §2.4 的核心契约 `Index Version = Config Snapshot + Document Snapshot` 目前完全缺失。现状中「这次构建的输入是什么」由一条即时查询隐式决定：

```sql
SELECT count(*) FROM documents
WHERE knowledge_base_id = %s AND current_version_id IS NOT NULL
```

出处：`index_versions.py:229-231`（`finalize_building_version` 的覆盖完整性分母）。

这意味着**同一个 `index_version_id` 在不同时刻重跑，输入文档集合会静默改变**——规格 §2.4 末段明确禁止的情形。

建议接入点（P0A 最小改动路径）：

1. `create_building_version()`（`index_versions.py:60`）增加必填 `document_snapshot_id` 参数。
2. `finalize_building_version()` 的分母从上述即时查询改为读 `document_snapshot_members`。
3. `active_or_bootstrap_version()`（`:134`）为 legacy 路径创建 `snapshot_completeness='partial'` 的快照——规格 §10.2.9。

这三处是 Snapshot 概念进入现有代码的**全部**入口，改造面可控。

---

## 6. 与规格冲突、需你裁决的项（规格 §18.1.5）

### 冲突 1：Rollback 后原 active 的去向

- 规格 §7：「当前 active → previous，目标 previous → active；两个指针在同一事务内交换」
- 现状 `rollback_to_previous()`（`index_versions.py:460-465`）：原 active → **`ready`**，不是 `previous`

现状注释给出的理由（`:447-449`）：同一知识库只允许一个 `previous`（partial unique index），且原 active 本身是放行过的版本，`ready` 语义正确。

这个理由**成立**。若按规格改为交换，需要先放宽 `one_previous_idx` 约束，会削弱「只保留一个回滚目标」的保护。**建议保留现状，规格 §7 相应调整**。

### 冲突 2：Build 的 1:N 改造涉及既有数据

`index_builds.operation_id` 是 `NOT NULL UNIQUE`（`0024`）。改为 1:N 后，`operations` 与 `index_builds` 的一对一绑定关系被打破——`operations` 是记录整个 Version 的构建意图，还是每次 attempt？规格未明确。**需要你定**，因为它决定 `operations` 层是否还有存在必要。

### 冲突 3：`index_definitions` 的现有行

V25 迁移已为每个存量 `index_version` 插入了一条派生 definition（`0024` 的 INSERT ... SELECT）。升为真配置源后，这些行的 `vector_config` 等四个字段是**伪造值**（写死字面量），按规格 §10.1 应标为 `config_completeness = unknown` 而非保留假值。

---

## 7. 风险

| 风险 | 说明 |
| --- | --- |
| 无 CI 覆盖的一致性约束会静默腐烂 | 本仓已发生五次（CLAUDE.md 第五条）。P0A 新增的每条跨表约束都必须同时加自动检查 |
| Schema 版本号涨了要同步改五处 | CLAUDE.md 第六条列出全部五处；只有第 1、2、5 处有自动校验 |
| 双实现只测一个 | CLAUDE.md 第四条。P0A 若引入领域服务层，JSON 与 Postgres 两套仓储必须跑同一组断言 |
| 迁移期双读核对无现成工具 | 规格 §10.2.6 要求双读核对，现仓无此机制，需在 P0A 一并建立 |
| 前端 9 格 Stepper 会先"变得更空" | 阶段语义落地前，新增的 `parse`/`chunk` 等 stage 写入是分批的，中间态会出现更多灰格 |

---

## 8. P0A 文件级实施计划

规格 §16 P0A 共 9 步。按本仓实际结构落到文件：

**步骤 1（本文档）** ✅ 只读盘点。

**步骤 2 — 新增治理表**
- 新建 `backend/migrations/0026_index_governance_core.sql`：`document_snapshots`、`document_snapshot_members`、`validation_reports`、`index_lifecycle_events`；`index_builds` 加 `attempt_no` 并改唯一约束；`index_versions` 加 `document_snapshot_id` / `definition_id` / `legacy_migrated` / `config_completeness`；`document_index_states` 加 `parse_status` / `chunk_status` / `embedding_status`
- 同步五处版本号：`backend/app/config.py:33`、`.env.example`、`docker-compose.yml`、`docker-compose.release.yml`、`deploy/kubernetes/configmap.yaml`、`deploy/kubernetes/workloads.yaml`

**步骤 3 — 拆分领域服务**
- 新建 `backend/app/index_governance/`：`definition_service.py`、`version_service.py`、`build_service.py`、`validation_service.py`、`activation_service.py`
- 拆解 `pipeline_governance.py`（435 行）与 `index_versions.py`（514 行），**消除三个循环依赖**——这是拆分能否成立的验收点

**步骤 4 — 组件绑定同一 Version**
- `chunks` 已有 `index_version_id`；需核查 `lexical.py`（关键词）与 metadata 过滤路径是否同源

**步骤 5 — RetrievalVersionResolver**
- 改 `postgres_documents.py:55-62`，把 5 处独立解析收敛为请求级一次解析
- 影响 `service.py` 的检索入口签名

**步骤 6 — 三层 Validate**
- 新建 `backend/app/index_governance/validation/`：`integrity.py`、`technical.py`、`retrieval_quality.py`
- 第三层复用既有 `backend/evaluation/report.py`，前两层为全新实现

**步骤 7 — Activate/Rollback 扩展**
- 在 `switch_to_version()` 既有事务内增加组件版本校验与 Validation Report 绑定
- **不重写**该函数的三条 UPDATE 顺序（注释已记录实测理由）

**步骤 8 — Legacy 回填**
- 新建 `scripts/backfill_index_governance.py`，支持 dry-run / 幂等重跑 / 异常清单
- 为现有 active version 建 `snapshot_completeness='partial'` 的 legacy snapshot

**步骤 9 — 测试**
- 新建 `backend/tests/test_index_governance.py`、`test_document_snapshot.py`、`test_validation_gates.py`
- 扩 `test_postgres_foundation.py`（V26 迁移幂等）、`test_pgvector_integration.py`（单请求版本一致性）

---

## 9. 待确认

进入 P0A 前需要你回答 §6 的三项冲突。§4-C 的凭证存储形态需单独核查后再定。

本阶段未做任何修改。工作区状态：`git status` 干净，分支 `main`。

---

## 10. 盘点后的更正（2026-09-05，阶段 0 实施中发现）

三处结论要更正，都是实施时读到更多代码才确认的：

1. **§4-C 凭证存储：已核查，无问题。** `_read_credentials()` 从**环境变量**读取
   （配置项 `credential_env` 只存变量名），docstring 明确写着「凭据绝不进数据库：
   写进 configuration 会让数据库备份、审计 payload 和只读数据源接口同时变成密钥泄露面」，
   缺失时明确失败而不回退匿名访问。符合规格 §3.1，不需要改造。

2. **§4-B 的「三处投影反向读取」实际只有一处。** `postgres_repositories.py:247-254`
   那两处是同一个列表查询里的两个只读子查询，属于读投影做展示——投影的正常用法，
   不是污染。真正把投影值写回另一张表的只有
   `postgres_documents.py` 那处 `failure_stage=(SELECT current_stage FROM operations …)`，
   已在阶段 0 修正为固定的 `'build'`。

3. **schema 版本号腐烂比 §7 记的更严重。** `docker-compose.release.yml` 与
   `deploy/kubernetes/workloads.yaml` 停在 **17**，而 `config.py` 是 25——落后 8 个版本。
   `scripts/validate_kubernetes.py` 有对应检查且能抓到，说明它没有进 CI，或进了但一直红着。
   这正是 `CLAUDE.md` 第五条描述的腐烂模式，第六次。阶段 0 已把六处统一到 26。
