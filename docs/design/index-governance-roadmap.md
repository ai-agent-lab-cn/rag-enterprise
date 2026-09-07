# 索引治理与数据同步分层：梳理与实施规划

日期：2026-09-05
基线 commit：`8052a0f`（Schema V25）
依据：`index-m.md`（实施规格）、`索引治理生产链.md`（状态机主干）
前置盘点：[`p0a-layering-inventory.md`](./p0a-layering-inventory.md)

本文档是规划，不含改动。

---

## 1. 核心诊断：状态机装不下语义，溢出物长成了假分层

现状 `index_versions.status` 的 CHECK 只有 6 个值（`0010_index_versions.sql:9`）：

```
building, ready, active, previous, retired, failed
```

生产链要求 10 个：

```
draft → building → validating → ready → active → previous → retired → cleaned
失败：build_failed、validation_failed
```

缺的不是四个枚举值，是**四段语义无处安放**：

| 生产链状态 | 现状缺失后语义落在哪 | 出处 |
| --- | --- | --- |
| `draft` | 不存在。Version 创建即 `building` | `index_versions.py:87` 直接 INSERT `'building'` |
| `validating` | 挤进调用方。验证发生在激活那一刻，不是独立阶段 | `switch_to_version` 接收内存对象 `RetrievalEvaluationReport`，`index_versions.py:322` |
| `build_failed` / `validation_failed` | 合并为单一 `failed`，两条恢复路径不可分 | `finalize_building_version:245` |
| `cleaned` | 挤进 `retire_version`。它删分块但状态停在 `retired` | `index_versions.py:510-513` |

### 1.1 最严重的一处：`ready` 兼任两个互斥含义

- **未验证的 ready**：`finalize_building_version:245` —— 构建覆盖完整即置 `ready`
- **已验证并上线过的 ready**：`rollback_to_previous:461` —— 回滚时原 active 降级

两者在库里无法区分。而 `switch_to_version:353` 只检查 `status == 'ready'` 就进入放行流程，真正的质量门禁（`:359-385`）检查的是**调用方传入的内存对象**。

结论：**门禁不在状态机里，在调用方手上。** 指纹比对（`:380`）能挡住「用 A 配置的报告放行 B 配置的索引」，但挡不住「由谁决定是否验证」。生产链图里 `ready` 只有 `validating → pass` 一个入口，这条约束目前完全不存在。

### 1.2 统一解释

盘点报告里的四个断层——三代任务抽象并存、`index_definitions` 是派生装饰层、`document_index_states` 三条 lane 恒等、模块循环依赖——根因同一个：

**状态机只有 6 格却要装 10 段语义，溢出的部分长成了 `operations` / `index_builds` / `document_index_states` 这些事后聚合层。**

它们不持有写权，只在事实发生后去聚合，因此永远不可能成为真正的分层。

所以改造主轴是**把语义放回状态机**，不是继续加表。

---

## 2. 对规格的三处调整建议

### 调整一：P0A 之前插入「地基阶段」，只拆不建

规格 P0A 的 9 步混了两类工作：补缺失实体（Snapshot / Validation Report / Build attempt）与修假分层（派生 definition、恒等 lane、混读版本、循环依赖）。后者是前者的地基。

V5-0024 已经演示过一次「加一层而不迁移职责」的结果。带着三个循环依赖和 136 处裸连接去建 `document_snapshots`，产出的会是第四代装饰层。

**地基阶段的验收标准必须是「旧的那套消失了」，不是「新的那套建起来了」。** 这条可自动化：循环依赖用 import 图扫描，版本混读用单请求断言。

### 调整二：`document_revisions` 不新建表，扩 `document_versions`

规格 §13 要求新建 `document_revisions`。现状 `document_versions`（`0001:66-86`）已有 `version_number`、`content_sha256`、五条唯一约束和完整状态机，语义重合约 90%，缺的只有 `source_uri` / `source_etag` / `source_modified_at` / `sync_run_id` 四个来源追踪字段。

新建表意味着全量数据迁移 + 双写期 + `chunks` 外键改指向，风险远大于收益。

### 调整三：`sync_change_sets` 与 `index_lifecycle_events` 降到阶段 4

前者的信息可从 `sync_resource_runs` 聚合（缺的是集合级事实，不是数据）；后者 `audit` 表已在记录，缺的是 `actor_id` 不再硬编码为 `None`（`index_versions.py:423`）。两者都不是任何门禁的前提，提前建只会增加地基阶段的搬迁量。

---

## 3. 二十步规划

### 阶段 0 · 地基（4 步）— 不新增任何实体 ✅ 已完成（2026-09-05）

| 步 | 内容 | 实际结果 |
| --- | --- | --- |
| 1 | 删 `index_definitions` | 迁移 `0026`；连带删除 `ensure_index_definition`、只读路由与前端「治理配置」弹框 |
| 2 | 消除循环依赖 | 三个环全部消失；12 处延迟导入清到 1 处（`index_versions.py` 的 TYPE_CHECKING） |
| 3 | 收敛版本解析 | 新增 `resolve_active_version`；一次召回从最多 6 次解析降为 1 次 |
| 4 | 假 lane 与投影污染 | UI 三列合一；`failure_stage` 不再读投影 |

**验收结果**：`test_module_boundaries.py` 守卫无环与无延迟导入（68 项）；
`test_one_retrieval_resolves_the_index_version_exactly_once` 守卫单次解析；
后端 432 passed / 新增失败 0（基线 356 passed），顺带修好 7 个既有失败。

与原计划的三处偏差：

- **第 1、2 步顺序对调。** 删 `index_definitions` 使 `index_versions ⇄ pipeline_governance`
  这个环**自动消失**——`ensure_index_definition` 是那条反向依赖的唯一来源。先删表再拆环，
  拆环的工作量少一个。
- **没有新建 `index_governance/` 包。** 环解开后依赖已是单向链
  （`postgres_documents → data_source_sync → postgres_repositories → pipeline_governance → index_versions`），
  再拆包只是搬文件，不改变依赖方向。按最小方案留到阶段 1 需要新增服务时一起做。
- **第 4 步不补 `parse`/`chunk`/`embedding` 三列。** 索引写入是一个原子事务，三条 lane
  恒等是**如实反映**而非缺陷；真正的问题是 UI 把它们画成三个独立阶段。因此改法是
  UI 三列合一 + 代码注释说明，数据库结构原样留给阶段 1 拆分 build 阶段时使用。

### 阶段 1 · 状态机与索引治理闭环（7 步）— 规格 P0A

这七步是一个不可分割的闭环，对应生产链图的全部主干。

| 步 | 内容 | 状态 |
| --- | --- | --- |
| 5 | `document_snapshots` + `document_snapshot_members` | ✅ 迁移 `0027` |
| 9 | `index_builds` 加 `attempt_no`，1:1 改 1:N | ✅ 迁移 `0028` |
| 10 | `cleaned` 状态；`retire` 与 `cleanup` 拆成两个动作 | ✅ 迁移 `0029` |
| 7 | `validation_reports` 表 + 三层门禁 | ✅ 迁移 `0030`，新增 `index_validation.py` |
| 6 | 状态枚举 → 生产链的状态集 | ✅ 迁移 `0031` |
| 8 | **`ready` 语义迁移** | ✅ 门禁从调用方搬进状态机 |
| 11 | Legacy 回填脚本 | ✅ `scripts/backfill_index_governance.py` |

**阶段 1 完成。** 另补两处上一轮遗留：`POST/GET .../validations` 两个端点，以及版本详情
弹层里的三层门禁展示（逐项列出未通过的 check_key、期望值与实际值）。

**第 6 步实际落地 9 个状态，没有 `draft`。** 生产链图列了 10 个，但 `draft`（快照已创建、
尚未构建）在本仓没有产生者——`create_building_version` 一步就进入 building，没有「先建
候选版本、稍后再构建」的入口。加一个没有代码会写入的状态，就是阶段 0 刚清理掉的那类空枚举。
等真出现「创建候选但不立即构建」的需求时再加，届时它是真状态。

实施顺序按依赖调整为 **5 → 9 → 10 → 7 → 6 → 8 → 11**：第 6 步新增的 `validating` /
`validation_failed` 两个状态，其产生者是第 7 步的验证流程。先扩枚举再建产生者，中间那段
时间里它们就是「声明了但没有任何代码会写入」的空枚举——正是阶段 0 清理掉的那类假象。

**验收**（后端 447 passed，新增失败 0；前端与基线一致）：

- 第 5 步：覆盖「版本创建后新增的文档不影响其覆盖率判定」与「快照外的分块不能充抵覆盖率」
- 第 9 步：失败的构建重试时新开 attempt，第一次的失败记录保留
- 第 10 步：清理后版本进入 `cleaned`，回滚到已清理版本被 `INDEX_VERSION_ALREADY_CLEANED` 拒绝
- 第 7 步：完整性门禁能抓到「漏建一份文档」——而同一场景下检索质量层照样通过，
  这正是它单独无法发现漏建的证明；技术门禁能抓到维度声明与实际不符
- 第 8 步：直接改库把状态置成 `ready` 也激活不了，激活会核验持久化的验证报告

### 第 11 步挡住的一处升级事故

V31 把门禁搬进状态机后，`switch_to_version` 要求版本持有 status='pass' 的验证报告。
**升级前上线的版本一份都没有**——它们当年的放行依据是调用方传进来的内存对象，从未落库。
不回填的话，正在线上跑的 active 版本一旦回滚就再也切不回去，报 `VALIDATION_NOT_PASSED`。

`scripts/backfill_index_governance.py` 为 `active` / `previous` / `ready` 三种状态补一份
legacy 报告。**回填的是事实，不是结论**：三层结果一律 `unknown` 并注明无法追溯，
`report_source='legacy_backfill'`，页面显示「历史回填，非正式验证」。凭空补一份
「全部通过」才是伪造质量门禁。

`build_failed` / `validation_failed` / `cleaned` 不回填——它们本就不该被激活；脚本把它们
单列成异常清单打印出来，而不是静默跳过。

dry-run 缺省、`--apply` 才写入、可重复执行，三条都已实测。

### 第 8 步的连带修正：ACL 写扩散要覆盖新状态

`validating` 是一个「分块已建好、随时可能被激活」的状态，因此必须参与 ACL 与
`retrieval_status` 的写扩散——漏掉它的话，权限收紧之后该版本一旦激活就会生效一份过期 ACL。
借这次统一了五处写扩散的状态集合（此前只有一处包含 `ready`，其余三处没有，属于既有的不一致）：

`backend/app/data_source_sync.py`（2 处）、`postgres_repositories.py`（2 处）、
`postgres_documents.py`（1 处），现在一律是
`active / previous / building / validating / ready`。

**验收**（规格 P0A 结束条件）：不依赖新同步系统，从当前数据库文档生成 Snapshot，新 Version 能走通 `draft → building → validating → ready → active`，并原子回滚；线上不跨版本混读。

第 5 步的接入点在盘点报告 §5 已定位，只有三处：
`create_building_version:60`、`finalize_building_version:229-231`、`active_or_bootstrap_version:134`。

### 阶段 2 · 同步分层（4 步）— 规格 P0B，顺序有调整

| 步 | 内容 | 状态 |
| --- | --- | --- |
| 12 | 引导激活留痕（原计划「切断」，见下） | ✅ 迁移 `0032` |
| 13 | `document_versions` 扩四个来源字段 | ✅ 迁移 `0033` |
| 15 | tombstone 表 | ✅ 迁移 `0034` |
| 14 | 拆 `run_sync()`，建立事务边界 | ✅ 338 行 → 60 行编排 + 12 个单一职责函数 |

#### 第 14 步：测绘先于动刀

动手前用五个独立视角测绘了这 300 行（状态写入图 / 失败路径 / 事务边界需求 / 外部 IO /
测试覆盖）。测绘翻出的东西改变了这一步的做法：

**已修（都是真缺陷，不只是重构）**

1. **差异阶段每对象一个连接**。`upsert_sync_resource` 每次调用自建连接与事务，而差异阶段
   要为每个远端对象记一行——其中绝大多数是「无变化」。实测 12 个未变对象开 32 个连接。
   改为一次事务 `executemany` 后，对象数 4→16 时连接数增量从 12 降到 ≤3。
   守卫测试 `test_sync_cost_does_not_grow_with_unchanged_object_count` 测的是**增长率**
   而非绝对值——只断言一个常数上界，测的其实是固定开销，锁不住真正会退化的那一维。
2. **失败路径下 `operations` 行永不收口**。失败时只写 `data_sources` 与 `sync_runs`，
   而 `aggregate_sync_run`（唯一会更新 operations 的地方）只在成功路径上调用。实测：
   `sync_runs` 已是 `failed`，`operations` 仍停在 `queued`、`finished_at` 为空——
   前端任务列表显示一个永远排队、永远不报错的任务。这是「三代状态机分叉」的第四次现身。
3. **`conftest` 只守卫 `MINIO_ENDPOINT`，没守卫 `TEST_DATABASE_URL`**。CI 目前靠 workflow
   显式注入才没出事；那一行被删掉的话，几百条集成测试会一起静默跳过而日志全绿。
   按 CLAUDE.md 第五条补上。

**批评者查出、四个视角集体漏掉的**——它们都是 `run_sync` 与**外部代码**的耦合，
在函数内部看不见。已修最严重的一条：

4. **一次索引失败就能把整个数据源永久锁死。** 链路是确定性的，不是竞态：
   索引非终态失败 → 下次同步该对象归入 retry 分支 → `_retry_object` 撞上
   `index_jobs_one_active_version_idx` 返回 `None` → `if retried_version:`
   **没有 else**，那行 `sync_resource_runs` 停在 `discovered` →
   它不在 `TERMINAL_RESOURCE_STATUSES` 里，且 `document_version_id` 为 NULL，
   而 `update_sync_resource_for_job` 只按该列匹配——**没有任何代码路径能再推进它** →
   `aggregate` 的 `done` 永远为假，`sync_runs` 卡在 `indexing` →
   撞上 `sync_runs_one_active_source_idx`（`UNIQUE(data_source_id) WHERE status IN (…,'indexing')`）
   → 此后每次 `enqueue_sync` 都报「该数据源已有同步任务在进行中」，而实际上没有任何任务在跑。
   唯一解法是人工调 `cancel_sync_run`。

   修复分两半，各管一件事，**必须分别测**：
   - `else` 分支落终态 → 解开死锁
   - `_retry_object` 在「任务已在跑」时返回版本 id 而不是 `None` → 结论诚实

   我最初只写了前一半的断言，回退后一半时测试照样通过——因为终态确实落了，只是把一个
   **正在正常重试**的资源标成了「失败」。不死锁了，但报告是假的。两条断言现在都在。

**批评者查出、已修的另外三条**（都是静默故障：没有任何状态位会显示异常）

5. **内容回退 A→B→A 后检索侧永久返回旧内容。** `index_document` 按 `content_sha256`
   查既有版本时**不过滤 status**，回退时命中早已 `superseded` 的 v1，直接短路返回——
   不入队、不移动 `current_version_id`；而 `_record_object` 记下 hash(A)，下次同步判定
   unchanged。远端是 A，库里永久服务 B，且再也不会自愈。
   修法不能是「插一条同哈希新版本」——`UNIQUE (knowledge_base_id, document_id, content_sha256)`
   挡着；正确做法是把那个 superseded 版本重新入队，让它重新成为 current。
6. **同名文件先上传、后进同步目录，会每次同步都全量重索引。**
   `_known_objects(only_indexed=True)` 的 EXISTS 要求 `documents` 与 `data_source_objects`
   的 `data_source_id` 相等，而 `index_document` 的 upsert **只在 INSERT 时写这一列**。
   `document_id` 只由知识库与文件名折算，两条路径必然撞上同一个 id。
   **修复位置是关键**：认领动作必须放在幂等短路**之前**——内容一字未改时短路直接返回，
   后面的 upsert 根本跑不到。我第一版改了 upsert，测试照旧红。
7. **已同步的对象变成超限后被静默软删。** 跳过的键不会被 `list_objects` yield，
   于是「本地有、清单里没有」把它算成删除：软删文档、写墓碑、删对象记录；随后 skip 行
   又 ON CONFLICT 覆盖掉 delete 行，审计上只剩「跳过」。一次调低 `max_upload_mb`
   就能让线上文档静默退出检索——而代码注释声称的是「超限对象不入队、不软删、不进对象记录」。

**结构拆分结果**

`run_sync` 从 338 行降到 60 行编排，拆出 3 个 dataclass（`SyncContext` / `Discovery` /
`SyncPlan`）与 12 个单一职责函数。事务边界由外部 IO 天然划定，不是风格选择：

`_load_context` → `_discover`（跨 `list_objects`，代价不可预估，任何事务都不能跨过它）
→ `_plan`（纯计算 + 熔断）→ `_commit_plan`（**一次事务**写完整份差异清单）
→ `_process_changed` / `_process_retries`（每对象内含 IO，无法收进一个事务）
→ `_apply_deletions` → `_reconcile_present` → `_record_skipped` → `_finalize`

前置条件是先补治理层特征测试：`sync_resource_runs` 的逐行写入此前**零覆盖**，
删光所有 upsert 调用原有测试仍全绿。新增两条覆盖「每个对象的操作与终态」和
「单对象失败进 dead_letter 而不拖垮整批」。

**测绘的两条「承重顺序」断言，我逐条实测，一条被证伪：**

| 断言 | 实测 |
| --- | --- |
| `_reconcile_present` 必须在 `_apply_deletions` 之后，否则软删会被立刻撤销 | **不成立**。反转后 34 个测试全过。两种顺序最终状态相同：反转时对象先被标 searchable、随后又被删除阶段标回 deleted；墓碑那侧要撤的记录尚未写入，是空操作。已改注释，保留现序的理由改为「依赖干净快照比依赖后写覆盖前写稳健」 |
| `_finalize` 里写 `discovered_cursor` 必须先于 `aggregate_sync_run` | **成立**。反转后 3 个测试红——`committed_cursor` 是 aggregate 从 `discovered_cursor` 抄的，顺序反了会抄到 NULL |

差点把一条未经证实的说法当既定事实写进代码注释。**agent 的分析要逐条验证，
尤其是「这里不能改，改了会坏」这类——它们最容易被当成不必再查的结论。**

**已修：长同步会被判僵死并重复执行**

`recover_stale_jobs` 把 `status='running'` 且 `locked_at` 超过 900 秒的任务原地改回
`queued`，而 `run_sync` 全程不刷新租约——一次同步的耗时没有上界（`list_objects` 要读完
每个文件算哈希，逐个对象还要 fetch 加索引）。超时后任务被另一个 worker 领走，
**两份 run_sync 并发跑在同一个 sync_run_id 上**，互相覆盖 `data_source_objects`，
先跑完的那份还会提前释放同步锁。

两个唯一索引都拦不住：`index_jobs_one_active_sync_idx` 与 `sync_runs_one_active_source_idx`
防的是「两条不同的记录」，而这里自始至终是同一行。

修法是让 `_ensure_sync_active` 兼做心跳——它本来就在每个对象边界被调用，而取消检查与
续租问的是同一个问题：「我还该继续吗」。答案为是时就该让别人知道我还活着。

写这条测试时踩了两次坑，都值得记：
1. 第一版在 `run_once()` **之后**检查回收，而那时任务已是 `succeeded`，回收器自然
   一无所获——测试通过但理由是错的。
2. 第二版在单次 fetch **内部**拨回租约后立刻检查，中间没有检查点可插入——那是真实
   无法避免的窗口，不是心跳能解决的问题。真实场景是「跨很多对象累计超时」，
   最终版改成第一个对象拨回租约、后续对象验证已被续上。

另外核实了 `index_jobs.sync_run_id` 列确实存在——否则那条 UPDATE 会静默匹配不到任何行，
测试同样会「通过」。

**测绘查出、尚未处理的**

| 发现 | 影响 |
| --- | --- |
| `sync_resource_runs` 的逐行写入**零测试覆盖**——删光所有 upsert 调用测试仍全绿 | 结构拆分前必须补特征测试，否则整个治理投影可以被悄悄改坏 |
| `_ensure_sync_active` 五个检查点与 `SYNC_CANCELLED` 零覆盖 | 同上 |
| 单资源异常吞掉（dead_letter 分支）零覆盖：没有任何测试让 `run_sync` 内部抛过异常 | 同上 |
| Web 与 ReadOnlyDatabase 连接器是**有状态的**：`fetch`/`metadata`/`skipped` 依赖 `list_objects` 时建的内存缓存 | 拆函数时连接器实例必须原样传递，不能重建 |
| `list_objects()` 无重试，而代价小得多的单对象 `fetch` 有三次重试 | 重试装反了 |
| `present` 重读必须晚于 `_forget_objects`，否则 `mark_documents_searchable` 会立刻撤销软删 | 承重的隐式顺序，代码里没有注释说明 |
| 只在大小写上不同的两个对象键共享同一个 `document_id` | 删一个会让另一个退出检索 |
| 改数据源 configuration 对存量对象零效果 | `_apply_governance_metadata` 的 docstring 承诺够不到——它的触发前提是对象 version 变了，而 local_directory 的 version 就是内容哈希 |

**结构拆分的目标形态**（测绘给出的六个事务边界）：发现快照 → 差异提交（含熔断与软删除
前移）→ 每对象的 pre-IO / post-IO 两段 → 循环后对账 → 收口。外部 IO（`list_objects`、
`fetch`、`index_document`）天然把边界划在这几处，事务不能跨过它们。

**第 12 步「切断隐式激活」是错的判断，实施时改了。** `active_or_bootstrap_version` 不是
同步专有路径——所有首次索引都走它（`postgres_documents.py` 的非 rebuild 分支）。切断会让
用户上传第一份文档后知识库不可检索，直到有人手工跑评测并激活，那是产品行为倒退。

改为**留痕不阻断**：首版仍直接可用，但会写一份 `report_source='bootstrap'` 的验证报告，
三层结果一律 `unknown`。报告必须存在——否则回滚到首版之后就再也切不回来（激活要求持有
pass 报告）；三层必须是 unknown——首版确实没跑过门禁，标 pass 就是伪造。

### 阶段 2 顺带修好的：整条同步链路

`test_sync_pipeline` 与 `test_s3_sync` 在基线上有 9 个失败，全部修复。根因是三个 bug，
**其中两个是我在阶段 0 引入的**：

1. `run_sync(settings, job, self)` 里的 `self` 是 `IndexWorker`，而它没有 `index_document`
   ——阶段 0 做依赖倒置时，我误以为 `_process` 属于 `PostgresAsyncRAGService`。
   现在 `IndexWorker` 持有一个 service 实例专门用于索引。
2. `metadata["source_modified_at"] = item.modified_at` 把 `datetime` 放进了要 Jsonb
   序列化的字典。改存 ISO 字符串，写回 timestamptz 列时由 Postgres 解析。
3. **既有 bug，且是本文档 §1 那个诊断的实证**：`aggregate_sync_run` 把同一个聚合结果
   写进三张表，而三张表的状态域各不相同——

   | 表 | 进行中的取值 |
   | --- | --- |
   | `sync_runs` | `indexing` |
   | `operations` | `running`（无 `indexing`） |
   | `data_sources.last_sync_status` | `running`（无 `indexing`，且无 `partial_failed`） |

   直接塞过去会违反后两张表的 CHECK，整个 aggregate 事务回滚，同步任务卡在 queued
   反复重试。而表面症状是「该数据源已有同步任务在进行中」——离真正的原因隔着两层。
   这就是「三代任务抽象并存、各有独立状态机」的实际代价。

第 4 个是测试腐烂：`test_disabled_source_cannot_start_sync` 改的是 `enabled`，而 V25 起
`enqueue_sync` 看的是 `sync_enabled`。这条守卫从 V25 之后就没有生效过。

### 验证方法的一个盲区

前两个 bug 逃过了我每一步的回归检查，因为我的判据是「相比基线新增的失败 = 0」——而这些
测试**在基线上本来就是失败的**。已经红着的测试换一个原因继续红，这个判据看不出来。

补救：涉及某条链路的改动，要单独把那条链路的测试跑到全绿，而不是只看总数差异。

### 阶段 3 · 编排衔接 — 规格 P0C，两步都作了调整

| 步 | 规格原文 | 实际 |
| --- | --- | --- |
| 16 | Change Set 落表；`Sync completed → Snapshot Candidate` 显式衔接 | ✅ 追溯要求已由第 13 步满足，无需新表 |
| 17 | 策略化自动 Build / Validate | ❌ 砍掉，替换为**配置漂移检测** |

#### 第 16 步：追溯链已经通了，不必再建表

规格 §18.3 要求「Sync Run → Document Snapshot → Index Version 的关联可追踪」。第 13 步加的
`document_versions.sync_run_id` 已经把这条链接通，实测可查：

```sql
sync_runs → document_versions(sync_run_id) → chunks → index_versions
```

剩下的 `sync_change_sets` 落表在规划「调整三」里已论证过——信息可从 `sync_resource_runs`
聚合，不是任何门禁的前提。

#### 第 17 步：规格写的那个自动编排没有触发条件

规格假设「同步产生候选版本 → 自动构建验证」。而本仓的设计是同步**增量写入 active 版本**，
同步完成时配置没有变化，没有任何东西需要重建。按规格严格读（文档集合变了就是新版本）
则每同步一份资料都要走完整验证周期，操作上不成立。

**替换为配置漂移检测。** 它有真实的触发条件——配置变了，而这正是唯一真正需要新索引版本的情况。

此前 `config_fingerprint` 只用于两件事：创建版本时算一次，验证时比对「评测报告评的是不是
这个版本的配置」。**没有任何代码拿 active 版本的指纹与当前配置比对**——改完 `chunk_size`
之后线上索引仍是旧配置建的，可以无限期这样跑：不报错、不提示、页面上看不出来。
与 CLAUDE.md 第五条记的那几次腐烂是同一个形状。

**只报告，不自动重建。** 全量重解析加重嵌入比备份贵得多，而这个项目已两次拒绝隐式的昂贵
动作：`validate_kubernetes.py` 的「禁止隐式定时备份」硬检查，以及 V5-5 拒绝索引版本自动
过期清理（见 `v5-6-sync-pipeline.md` §2）。什么时候重建由操作者决定。

报告逐项列出差异而不只给布尔值——操作者要据此判断值不值得重建，「有问题」三个字给不了这个判断。

两处实现细节值得记：
- 差异明细写成**可见文本**而非 `title` 属性。原生 tooltip 悬停约一秒才出现、触屏上完全
  看不到（CLAUDE.md 第一条），而这条提示的全部价值就在于说清「哪一项变了」。
- 最初用了 `text-warning-text`，但 tailwind.css 里只有 `--color-warning`，那个 utility 会被
  静默丢弃。改用真实令牌后**读构建产物**确认：`text-warning{color:var(--color-warning)}`。

### 阶段 4 · 前端与可观测 — 规格 P1

| 步 | 内容 | 状态 |
| --- | --- | --- |
| 18 | 阶段语义落地；`PipelineStepper` 与后端 stage 对齐 | ✅ |
| 20 | `index_lifecycle_events` + actor 真实记录 | ✅ 迁移 `0035` |
| 19 | 版本详情：门禁结论 + 生命周期时间线 | ✅ |

#### 第 18 步：问题不是「格子太多」，是阶段没人记

原以为要砍掉那些永不点亮的格子。查下来相反——后端**确实**走了
parse/chunk/vector/keyword/metadata/validating 六个阶段，`mark_stage` 也确实在记录，
但它有个前置条件 `if job.get("operation_id")`，而 rebuild 与 sync 任务的 `INSERT INTO
index_jobs` 都没传这一列。阶段被算出来了，只是没人记。

修了 `enqueue_rebuild` 传 `operation_id` 之后阶段才开始落地，然后才把前端格子对齐到真实词汇。

`PipelineStepper` 还有个隐蔽行为：stage 匹配不上时它**退回按 `progressPercent` 猜位置**
（`currentIndex` 的 `inferredIndex` 分支），不是显示「未开始」。所以此前那些格子不是灰着，
是在拿硬编码百分比猜。`test_module_boundaries.py` 里加了一条守卫比对两边词汇。

#### 第 20 步：审计记了动作，记不下人

版本表只保留「现在是什么状态」，三个时间戳各自只记最后一次。唯一的留痕是通用 audit 表的
两条记录，而它 **`actor_id` 恒为硬编码的 `None`**（`index_versions.py` 两处）——出事时只能
知道「有人激活了它」，不知道是谁，也看不到前后状态。

`index_lifecycle_events` 是 append-only 的：回滚不是撤销一条记录，是再追加一条方向相反的
事件。`Actor` 从路由层一路传到事件与审计，`actor_id` 为空从此明确表示「系统自动触发」
（worker 收口构建、首次索引引导），而不是「不知道是谁」。

#### 第 19 步：版本详情

规格 §12.3 要五个 Tab。实际做成一个弹层里的三段——配置快照、发布门禁（逐项列出未通过的
check_key 与期望/实际）、生命周期时间线。Tab 是为内容多到装不下时准备的，这里三段加起来
不到一屏，拆成五个 Tab 只会让人多点四次。构建记录那一段等 attempt 真的多起来再说。



| 步 | 内容 |
| --- | --- |
| 18 | 阶段语义落地；`PipelineStepper.tsx:39-47` 九格与后端 stage 对齐 |
| 19 | 版本列表 + 五 Tab 详情页 + 生命周期时间线 |
| 20 | `index_lifecycle_events` + `sync_change_sets` + actor 真实记录 |

---

## 4. 前端连带影响（阶段 1 第 6 步的隐藏成本）

状态枚举 6 → 10 会直接影响前端。现状问题已在盘点报告 §3 记录：

- `sync_resource_runs` 声明 14 个 status，代码只写入 8 个；`parsing` / `chunking` / `enriching` / `validating` / `activated` / `discovered` 六个永不出现
- `PipelineStepper.tsx:39-47` 为 sync 画九格，其中 `parse`/`chunk`/`enrich`/`validate`/`activate` 后端永不写入
- 后端实际写入的 `size_limit`、`retry_wait`、`fetch_or_normalize` 前端无 alias 接得住

**阶段 1 期间前端会短暂"更空"**：新状态陆续落地时，中间态会出现更多灰格。这是预期现象，不是回归。

---

## 5. 风险

| 风险 | 依据 |
| --- | --- |
| 无 CI 覆盖的一致性约束会静默腐烂 | 本仓已发生五次（`CLAUDE.md` 第五条）。每条新增跨表约束必须同时加自动检查 |
| Schema 版本号涨了要同步改五处 | `CLAUDE.md` 第六条；只有第 1、2、5 处有自动校验 |
| 双实现只测一个 | `CLAUDE.md` 第四条。若引入领域服务层，JSON 与 Postgres 两套仓储须跑同一组断言 |
| 状态枚举迁移是破坏性改动 | 阶段 1 第 6 步涉及 CHECK 约束、全部读写点、前端映射。越晚做代价越大，因此排在阶段 1 前段 |
| 迁移期双读核对无现成工具 | 规格 §10.2.6 要求，本仓无此机制，需在阶段 1 第 11 步一并建立 |

---

## 6. 待裁决

| # | 事项 | 阻塞哪一步 |
| --- | --- | --- |
| 1 | `index_definitions` 升真相源 / 降视图 / 删 | 阶段 0 第 3 步。同时决定 Snapshot 挂 Definition 还是挂 Version |
| 2 | Rollback 后原 active 去向：规格要求 `previous`，现状是 `ready` | 阶段 1 第 8 步 |
| 3 | Build 改 1:N 后 `operations` 的去留 | 阶段 1 第 9 步 |

### 裁决 2 的现状理由

`rollback_to_previous:447-449` 的注释给出理由：同一知识库只允许一个 `previous`（partial unique index），且原 active 本身是放行过的版本，`ready` 语义正确。

**这个理由成立。** 按规格改为交换需先放宽 `one_previous_idx`，会削弱「只保留一个回滚目标」的保护。建议保留现状、相应调整规格 §7。

注：`ready` 语义在阶段 1 第 8 步拆分后，此处应改为降到「已验证通过」那一支，而非当前的混合态。

### 裁决 3 的背景

`index_builds.operation_id` 现为 `NOT NULL UNIQUE`（`0024`）。改 1:N 后，`operations` 是记录整个 Version 的构建意图，还是每次 attempt？规格未明确。这决定 `operations` 层是否还有存在必要——若它只是每次 attempt 的镜像，应当直接删除，避免第四代装饰层。

---

## 7. 未核查项

`_read_credentials()`（`data_source_sync.py:193`）从 `configuration` 读取凭证。规格 §3.1 要求凭证值不得落入普通配置。**本次未验证其实际存储形态**，阶段 2 启动前需单独确认，此处不下结论。
