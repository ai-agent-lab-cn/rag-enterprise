# V5-6 设计：数据同步 Pipeline

日期：2026-08-28
基线 commit：`2769bad`（Schema V10，Chroma 已移除，向量存储只有 pgvector）
上游归属：[#92 V5 总控](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/92) 第 4 项
「企业数据源：先定义连接器协议，再实现一个可真实验收的外部来源及增量同步」的**前半部分**；
范围由 [#98](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/98) 的
「不扩展多数据源同步 Pipeline，该能力进入 V5-6」界定。

**本阶段不达成 #92 第 4 项。** 该项要求「一个可真实验收的**外部**来源」，而本阶段的连接器
是本地目录，不是外部来源。第 4 项要等 V5-7 接入 S3 兼容对象存储后才算达成。Issue 与文档
里不得把「支持数据同步」表述为「支持企业数据源」。

## 1. 问题

资料只能人工上传。文档改了要重新上传，文档作废了要手工删除。企业场景里文档动辄几百上千份
且持续变化，靠人维护知识库与数据源的一致性不成立——而「检索到已经作废的内容」是企业 RAG
最直接的事故来源。

现状缺口（以 `2769bad` 代码为准）：

| 缺口 | 出处 |
| --- | --- |
| `data_sources.configuration` 是纯预留空壳 | 全仓无任何读写点 |
| `source_type` 的 `object_storage` / `web` / `connector` 三个枚举值不对应任何实现 | `0001_postgres_foundation.sql:45` |
| 没有对象状态记录，无法判断「上次同步时看到了什么」 | 无相关表 |
| `index_jobs.job_type` 只有 `index` / `rebuild` | `0003_index_rebuild.sql:7` |
| 同步状态是从 `index_jobs` 派生的计算字段，表达不了「同步成功但无变化」 | `postgres_repositories.py` 的 `j.finished_at AS last_synced_at, j.status AS sync_status` |
| 没有连接器抽象，file 类型的逻辑硬编码在 API 上传流程里 | `main.py` 的上传路由 |

## 2. 目标与非目标

**目标**

- 定义连接器协议，使后续接入新数据源不需要改动同步框架。
- 实现本地目录连接器：挂载的 NFS 共享、企业网盘的本地同步目录、定期落盘的导出文件，
  这些在企业里就是真实的资料入口。
- 增量同步：识别新增、内容更新、删除三类差异，只处理变化的对象。
- 删除同步带熔断与软删除双重保护。
- 全流程进 CI，且不依赖任何外部服务。

**非目标**

- **不接入外部数据源。** S3 兼容对象存储归 V5-7，届时它将成为协议的第二个真实实现，
  用来检验本阶段的抽象是否正确（见第 3 节末）。
- 不引入任何新的运行时依赖。本阶段全部用标准库。
- 不做定时同步。项目的既定原则是不引入隐式定时任务——`scripts/validate_kubernetes.py`
  有一条硬检查「本阶段禁止隐式定时备份，备份必须由操作者显式执行」，V5-5 也按同样理由
  拒绝了索引版本的自动过期清理。同步由操作者显式触发。
- 不做 webhook / 事件驱动同步。
- 不做多目录、不做通配匹配。一个数据源 = 一个根目录。
- 不做硬删除。同步只软删除，物理删除仍由人显式执行。
- 不改 V2/V3 的检索与回答算法基线。
- 前端不实现。

## 3. 连接器协议

```python
class SourceObject(NamedTuple):
    key: str                       # 数据源内唯一标识（本地目录：相对根目录的路径）
    version: str                   # 内容版本标识（本地目录：内容 SHA-256）
    size: int
    modified_at: datetime | None    # 仅用于展示，不参与任何判定


class Connector(Protocol):
    def list_objects(self) -> Iterator[SourceObject]: ...
    def fetch(self, key: str) -> bytes: ...
```

**协议只有两个方法，没有「增量拉取」。** 这是拿 S3 与 GitHub 两种未来实现压测后的结论：
GitHub 有 tree diff 能直接返回增量，S3 与本地目录都没有变更流。若协议提供
`list_changes(since_cursor)`，后两者的实现只能退化成「全量列举后自己算差异」，那这个方法
就是在骗调用方。因此**增量不进协议，而是框架层的能力**——列举比对对所有连接器一致，
连接器只负责回答「现在有什么」和「这个对象的内容是什么」。

`version` 是抽象的关键，它的契约是「**内容变了才变，内容没变就不变**」。
**不用 `modified_at` 做判定**：同内容重新落盘会刷新时间戳，内容改动也可能不改变 size，
两者都会误判。`modified_at` 只进展示层。

**`list_objects` 可能很贵，协议不掩盖这一点。** 本地目录要读完每个文件才能算出内容哈希；
S3 的 ETag 由服务端在列举响应里直接给出，一次 API 调用就够。同一个方法在两种实现下
成本差几个数量级。协议不为此增加「便宜的预检」之类的方法——那会把 S3 的特性泄进抽象。
调用方（同步框架）只在 sync job 里调用它一次，这个成本可以接受。

**返回迭代器而非列表**，让分页与流式读取在实现内部消化，框架层看不到分页概念。

**这个抽象的正确性在本阶段无法完全验证。** 只有一个实现时，任何抽象都可能藏着该实现
特有的假设。V5-7 接入 S3 时会成为第二个真实实现——本地目录的 version 是自己算的内容哈希，
S3 的是服务端给的 ETag，这个差异会立刻暴露协议里任何「假定 version 可以本地计算」的假设。
若届时发现协议需要调整，那是预期结果而非返工，本设计不为此预留兼容层。

## 4. 本地目录连接器

```json
{
  "root": "/mnt/enterprise-docs",
  "include_suffixes": [".md", ".txt", ".pdf"]
}
```

- `key` = 相对 `root` 的路径，保留 `/`。
- `version` = 文件内容的 SHA-256。
- 递归遍历 `root`，按 `include_suffixes` 过滤，跳过隐藏文件与符号链接。

**为什么 version 用内容哈希而不是 (size, mtime)**：mtime 会在同内容重新落盘时改变
（rsync、网盘客户端重传、`cp` 都会），那会触发不必要的重新解析与重新 embedding；
反过来，编辑后大小恰好不变的情况也真实存在。内容哈希是唯一满足 version 契约的选择。
代价是每次同步读全部文件——本地目录场景下这是磁盘 IO，可接受；这个成本差异已在第 3 节
记入协议契约。

**跳过符号链接**而不是跟随：跟随会引入目录环与越出 `root` 的读取面。

`root` 必须是已存在的目录且可读，否则同步以稳定错误码 `SOURCE_ROOT_UNAVAILABLE` 失败，
不静默产出空清单——空清单会被差异计算判定为「全部删除」，虽然有熔断兜着，但那是把
配置错误伪装成数据变更。

## 5. Schema V11

`source_type` 的 CHECK 增加 `local_directory`。不复用 `file`：那个取值的语义已被
「API 上传」占用，两者的同步行为完全不同（上传是推、目录是拉）。

`data_sources` 增三列，把同步状态从派生字段变为真实列：

```sql
ALTER TABLE data_sources
    ADD COLUMN last_sync_at timestamptz,
    ADD COLUMN last_sync_status text NOT NULL DEFAULT 'idle'
        CHECK (last_sync_status IN ('idle','running','succeeded','failed','aborted')),
    ADD COLUMN sync_failure_reason text;
```

派生字段表达不了「同步成功但没有任何变化」——那种情况不产生 index job，派生逻辑会
显示为 `idle`，与「从未同步」无法区分。`aborted` 专门表示熔断中止，与普通失败区分。

新表 `data_source_objects` 是比对的基础，记录上次同步时看到的每个对象：

```sql
CREATE TABLE data_source_objects (
    data_source_id text NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    object_key text NOT NULL,
    version text NOT NULL,
    -- 首次发现时索引尚未完成，此时还没有文档记录，因此可空。
    -- 知识库归属不在这里重复记录：data_sources 已有 knowledge_base_id（`0001:44`）。
    document_id text,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_source_id, object_key)
);
```

`ON DELETE CASCADE`：对象记录从属于数据源，与 V5-5 里 `index_versions` 对知识库用
CASCADE 同理——用 RESTRICT 会让同步过的数据源永远删不掉。

`index_jobs.job_type` 的 CHECK 从 `('index','rebuild')` 扩到 `('index','rebuild','sync')`。
sync 任务用 `data_source_id` 关联数据源，`document_version_id` 留空（它针对整个数据源
而非单个文档版本）。

**连带约束必须同步改写**：`0003` 那条 `index_jobs_rebuild_requires_batch` 写的是
`job_type = 'index' OR (rebuild_batch_id IS NOT NULL AND target_chunking_version IS NOT NULL)`，
新增的 `sync` 会落进后半句而被要求提供 rebuild 字段，因此改为
`job_type <> 'rebuild' OR (...)`。不改的话迁移能过，但插入 sync 任务会失败。

**同一数据源同时只允许一个活动 sync 任务**，由 partial unique index 保证：

```sql
CREATE UNIQUE INDEX index_jobs_one_active_sync_idx
    ON index_jobs (data_source_id)
    WHERE job_type = 'sync' AND status IN ('queued', 'running');
```

理由与 V5-5 的 building 唯一约束同理：两个 sync job 并发跑同一数据源会重复入队索引任务，
并互相覆盖 `data_source_objects`，导致对象状态记录与实际不符。放在数据库而不是应用层，
是因为 CLI 可能被并发调用，而 `0003` 已经用同样手法处理过版本级并发
（`index_jobs_one_active_version_idx`）。

## 6. 同步流程

一个 sync job 处理一个数据源，由现有 `index_worker` 消费：

1. 置数据源 `last_sync_status = 'running'`。
2. 读 `configuration`，构造连接器。
3. `list_objects()` 全量列举，得到当前清单。
4. 与 `data_source_objects` 比对，算**四**类差异：
   - **新增**：清单有、本地记录无
   - **更新**：两边都有但 `version` 不同
   - **重试**：有记录但当前版本没到 `ready`（解析或嵌入失败过）
   - **删除**：本地记录有、清单里消失

   **为什么需要「重试」这一类**：对象记录是在 `index_document` 返回后就写入的，而那时
   索引只是入队。若后续解析或嵌入失败，记录里已有 version，下次同步会把它当成「无变化」
   而永久跳过——文档在列表里一直显示失败，重跑同步毫无反应，也没有任何提示告诉操作者
   该怎么办。因此差异计算只把「当前版本已 ready」的对象算作已同步。

   重试**不能**走 `index_document`：它查到相同 `content_sha256` 的既有版本就直接返回
   （连 `failed` 状态的也返回），不会重新入队。重试走 `reprocess_version`
   （`postgres_repositories.py`），并且要取该文档**最新的**版本而不是
   `documents.current_version_id`——索引失败时指针根本没移动过，那个字段是 NULL。

   熔断的分母仍用全部记录：未 ready 的对象在远端依然存在，不该被算进「待删除」。
5. **熔断判定**（见第 7 节）。触发则中止，不执行任何写入。
6. 新增与更新：`fetch(key)` 拉内容，复用 `index_document` 的解析、切分、版本治理与
   异步索引链路，不新建平行实现。更新会产生新的 document version，旧版本按既有机制转
   `superseded`。**但该入口必须先改造，见第 6.1 节。**
7. 删除：软删除（见第 7 节）。
8. 更新 `data_source_objects`，置 `last_sync_status = 'succeeded'`、`last_sync_at = now()`。

**中断可续跑**：sync job 走 `index_jobs` 队列，继承 V5-4 的重试、租约超时恢复与幂等。
第 6 步为每个变化对象入队独立的 index job，因此 worker 在任意点崩溃后重跑，已完成的
对象因 `version` 已写入 `data_source_objects` 而被跳过。

**同步与索引解耦**：sync job 只负责「发现差异并入队」，实际索引由 index job 完成。
这样一次同步的失败面被限制在发现阶段，单个文档的解析失败不会让整次同步失败。

### 6.1 `index_document` 的两处必要改造

`PostgresAsyncRAGService.index_document` 当前有两处与同步场景直接冲突：

```python
safe_name = Path(filename).name                                  # 丢掉路径
document_id = _stable_id("doc", knowledge_base_id, safe_name.casefold())
source_id   = _stable_id("src", knowledge_base_id, safe_name.casefold())
```

1. **`Path(filename).name` 丢掉路径**，导致 `handbook/a/x.md` 与 `handbook/b/x.md` 算出
   同一个 `document_id` 互相覆盖。目录树里不同子目录下的同名文件非常常见。
2. **每个文件自建一个 data_source**（`src_<filename 哈希>`），而同步场景下所有对象都应
   归属那个目录数据源。不改的话 `data_source_objects` 的外键指向的数据源与实际索引产生的
   数据源不是同一个。

改造方式：`index_document` 增加可选参数 `data_source_id: str | None = None` 与
`relative_path: str | None = None`。都不传时行为与现在完全一致（API 上传路径不受影响）；
传入时用 `data_source_id` 作为归属、用 `relative_path` 作为 `filename` 与 `document_id`
的计算输入。

**对象键到文档的映射**：`filename` = 对象的 `key`（即相对根目录的路径），保留 `/`。
`documents` 的 `UNIQUE (knowledge_base_id, data_source_id, filename)`（`0001:63`）因此
成立。上传文件仍落在 `{kb}/{document_id}/{content_hash}{ext}`，与源目录结构无关，
所以不引入路径穿越面；但 `relative_path` 必须显式拒绝 `..` 与绝对路径，
错误码 `SOURCE_OBJECT_KEY_INVALID`。

## 7. 熔断与软删除

**熔断**：删除量**同时**满足两个条件才中止——

- `待删除数 > sync_delete_minimum`（配置项，默认 3）
- `待删除数 / 该数据源的已知对象总数 > sync_delete_threshold_percent`（配置项，默认 30）

触发时 sync job 置 `failed`、数据源置 `aborted`、待删清单写入 `sync_failure_reason`，
**不执行任何删除也不执行任何新增**。

**为什么要绝对下限**：纯比例阈值在小知识库上过于敏感——3 份文档删 1 份就是 33%，
10 份删 4 份就是 40%，而这些都是正常的日常操作。一个部门二十来份手册的知识库在企业里
很常见，纯比例会把日常删除全拦下来。加下限之后日常少量删除不再被拦，而配置错误导致的
批量删除在小知识库上同样会被抓住：20 份全部消失时删除数是 20，远超下限。

它挡的是配置错误而非真实删除——根目录被误改、挂载点掉了、导出任务没跑成功，都会让
列举结果几乎为空，差异算出来就是「全部删除」。连新增也一并不执行，是因为触发熔断的
典型原因就是「看到的清单不可信」，此时算出的新增同样不可信。

V5-5 的索引版本回滚救不了这种情况：那是索引层的回滚，文档记录本身的删除不在它的范围内。

对象总数为 0 时（首次同步）不做熔断判定——没有可删的东西。

**软删除**：把 `documents.metadata.retrieval_status` 置 `deleted`，并按 V5-5 第 8.2 节的
写扩散规则同步到 `active` / `previous` / `building` 三种状态的索引版本分块。`deleted`
这个取值 V5-3 已经预留在 `schemas.py:98,166` 的 Literal 里，检索侧
`retrieval_access.py:32` 已经在挡非 `searchable` 的分块，本阶段不新增过滤逻辑。

文档记录、版本记录和向量全部保留。物理删除仍只能由人显式执行。

**自动恢复**：对象重新出现时恢复 `retrieval_status = searchable`。

实现上它走的是「新增」路径而非「内容未变」路径——软删除时对象记录会从
`data_source_objects` 一并移除，否则该对象每次同步都会被重新算进「删除」，
永久污染熔断的比例分母。代价是重现的对象会被重新解析索引一次，即使内容没变。

这个代价是有意接受的：对象从数据源消失又出现，在企业场景里通常意味着「替换」而不是
「原样放回」，重新索引是更安全的默认行为。要做到「内容未变就不重新索引」需要给
`data_source_objects` 加 `deleted_at` 列并在比对时区分「已软删」与「从未见过」，
本阶段按 YAGNI 不做。

## 8. 依赖

**不引入任何新依赖。** 本地目录连接器只用标准库（`pathlib`、`hashlib`）。
S3 需要的 SDK 属于 V5-7 的决策。

## 9. 操作接口

- CLI `scripts/sync_data_source.py`：`create | list | sync | status`。
- 只读 API：既有的数据源列表接口增加同步状态字段，不新增端点。
- 触发同步的写接口本阶段不做：同步是高成本操作且带删除语义，先由 CLI 承担，
  与 V5-5 的索引切换同样处理。
- 前端不实现。AGENTS.md 要求「前端只展示后端真实可用能力」，同步触发与熔断确认
  需要单独一轮交互设计。

## 10. 测试

| 文件 | 覆盖 |
| --- | --- |
| `backend/tests/test_connectors.py`（新增） | 协议契约：内容相同但 mtime 变化时 version 不变；内容改变时 version 变；符号链接被跳过；`include_suffixes` 过滤；根目录不存在时 `SOURCE_ROOT_UNAVAILABLE`；`..` 与绝对路径的 key 被拒 |
| `backend/tests/test_sync_pipeline.py`（新增，需 PostgreSQL） | 首次全量；无变化空跑必须零 index job；新增；内容更新；删除软删；熔断触发且数据库无任何变更；sync job 中断后续跑跳过已完成对象；删除后对象重现则自动恢复 |
| `backend/tests/test_postgres_foundation.py`（扩） | V11 迁移幂等；改写后的 `index_jobs_rebuild_requires_batch` 允许插入 sync 任务；`REQUIRED_DATABASE_SCHEMA_VERSION` 断言改 11 |
| `backend/tests/test_pgvector_integration.py`（扩） | `index_document` 传入 `data_source_id` / `relative_path` 时归属正确、不同子目录同名文件互不覆盖；两参数都不传时行为与改造前一致 |
| `backend/tests/test_retrieval_access.py`（扩） | 软删除后分块不进检索；恢复后重新可检索 |

**全部测试不依赖外部服务**，只需既有的 PostgreSQL service。这是本阶段选本地目录而非
直接上 S3 的一个附带好处：本轮清理翻出四处腐烂（`run_baseline` 坏了一个大版本、
`evaluate.py` 静默假成功、K8s 版本号落后两个大版本且自相矛盾、`render.yaml` 描述已不
存在的架构），根因全是没有 CI 覆盖。

## 11. 验收

- Schema V11 可应用且幂等，`required_database_schema_version` 与 Compose、K8s 清单同步为 11
  （K8s 那三处版本号有 `validate_kubernetes.py` 的一致性校验兜着）。
- 一次完整演练：目录里放 5 个文档 → 首次同步全部索引 → 不改动再同步一次产生零 index job →
  改一个文档内容 → 同步只重建那一个 → 删两个 → 同步软删两个且检索不到 → 恢复其中一个 →
  检索恢复。全过程有审计记录。
- mtime 验证：`touch` 全部文件后同步，必须产生零 index job（证明 version 不受时间戳影响）。
- 熔断验证：删掉目录里 4/5 的文件后同步被 `aborted`，数据库无任何变更。
- 根目录不可用验证：把 `root` 指向不存在的路径，同步以 `SOURCE_ROOT_UNAVAILABLE` 失败，
  不产出空清单、不触发任何删除。
- 回归验证：`index_document` 改造后 API 上传路径行为与改造前逐项一致（既有 `test_api.py`
  与 `test_pgvector_integration.py` 全绿即为证据）。
- 后端测试与 Ruff、前端测试与构建、容器质量门全部通过。
- Issue 与文档如实写明「外部数据源接入属于 V5-7」，不把「支持数据同步」表述为
  「支持企业数据源」，也不把 `object_storage` / `web` / `connector` 三个枚举值宣传为已实现。
