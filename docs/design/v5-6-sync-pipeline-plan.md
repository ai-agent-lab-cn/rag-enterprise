# 数据同步 Pipeline 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库能从本地目录增量同步资料——自动识别新增、内容更新与删除，只处理变化的部分。

**Architecture:** 连接器协议只提供「列举」与「取内容」两个能力，增量是框架层的「列举比对」。sync 任务走既有 `index_jobs` 队列由现有 worker 消费，因此天然继承重试、租约恢复与中断续跑。删除走软删除（`retrieval_status = 'deleted'`）并带 30% 熔断。

**Tech Stack:** PostgreSQL 16 + pgvector、psycopg 3、Pydantic v2、pytest、Ruff。本阶段不引入任何新运行时依赖，连接器只用标准库 `pathlib` 与 `hashlib`。

**Spec:** `docs/design/v5-6-sync-pipeline.md`

## Global Constraints

- Schema 版本推进到 11：迁移必须命名 `backend/migrations/0011_data_source_sync.sql`，编号连续（`backend/app/database.py:14` 会校验），且 `Settings.required_database_schema_version` 同步改为 `11`。
- 同步改动 `docker-compose.yml` 的两处 `REQUIRED_DATABASE_SCHEMA_VERSION: "10"` 与 `docker-compose.release.yml` 的两处，以及 `deploy/kubernetes/configmap.yaml` 一处、`workloads.yaml` 两处——后三处有 `scripts/validate_kubernetes.py` 的一致性校验兜着，改错会被它报出来。
- 迁移在单事务内执行（`database.py:44`），禁止 `CREATE INDEX CONCURRENTLY`。
- 迁移文件一旦提交不得再修改内容：`schema_migrations.checksum` 会拒绝变更（`database.py:41`）。开发期改动需重建测试库。
- **`index_document` 的改造必须向后兼容**：两个新参数都不传时行为与改造前逐字节一致。既有 `test_api.py` 与 `test_pgvector_integration.py` 全绿是硬性验收条件。
- 所有 PostgreSQL 测试加 `@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")`。
- 不引入新依赖；不做定时同步；不做硬删除；前端不改。
- 中文注释与中文 commit message，技术标识保留英文（AGENTS.md）。
- 文档不得把「支持数据同步」表述为「支持企业数据源」——外部来源属于 V5-7。

---

## File Structure

| 文件 | 责任 |
| --- | --- |
| `backend/app/connectors.py`（新建） | `SourceObject`、`Connector` 协议、`LocalDirectoryConnector`。单文件即可，两个概念加一个实现，不建包 |
| `backend/app/data_source_sync.py`（新建） | 差异计算、熔断判定、同步编排。独立于 `postgres_documents.py`，因为后者已 1000+ 行 |
| `backend/migrations/0011_data_source_sync.sql`（新建） | Schema V11 |
| `backend/app/postgres_documents.py`（改） | `index_document` 加两个可选参数；`IndexWorker._process` 最开头分流 sync 任务 |
| `backend/app/config.py`（改） | `required_database_schema_version` 改 11；新增 `sync_delete_threshold_percent` |
| `scripts/sync_data_source.py`（新建） | CLI：`create / list / sync / status` |
| `backend/tests/test_connectors.py`（新建） | 连接器契约，纯 Python 不需数据库 |
| `backend/tests/test_sync_pipeline.py`（新建） | 同步端到端，需 PostgreSQL |

---

## Task 1: 连接器协议与本地目录连接器

**Files:**
- Create: `backend/app/connectors.py`
- Test: `backend/tests/test_connectors.py`

**Interfaces:**
- Consumes: 无（纯标准库）
- Produces:
  - `SourceObject(key: str, version: str, size: int, modified_at: datetime | None)`（NamedTuple）
  - `Connector` Protocol：`list_objects() -> Iterator[SourceObject]`、`fetch(key: str) -> bytes`
  - `LocalDirectoryConnector(root: Path, include_suffixes: tuple[str, ...])`
  - `validate_object_key(key: str) -> str`（拒绝 `..` 与绝对路径，抛 `AppError("SOURCE_OBJECT_KEY_INVALID")`）

- [ ] **Step 1: 写失败测试——version 必须只跟内容有关**

```python
def test_version_ignores_mtime_and_tracks_content(tmp_path: Path) -> None:
    """version 的契约是"内容变了才变"。mtime 会在同内容重新落盘时改变
    （rsync、网盘客户端重传、cp 都会），用它做判定会触发无谓的重新 embedding。"""

    target = tmp_path / "a.md"
    target.write_text("原始内容", encoding="utf-8")
    connector = LocalDirectoryConnector(tmp_path, (".md",))

    before = {item.key: item.version for item in connector.list_objects()}
    os.utime(target, (0, 0))
    after = {item.key: item.version for item in connector.list_objects()}
    assert after == before, "mtime 变化不得改变 version"

    target.write_text("改过的内容", encoding="utf-8")
    changed = {item.key: item.version for item in connector.list_objects()}
    assert changed["a.md"] != before["a.md"]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_connectors.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.app.connectors`

- [ ] **Step 3: 实现**

创建 `backend/app/connectors.py`：

```python
"""数据源连接器协议与本地目录实现。

协议只有"列举"和"取内容"两个能力，没有"增量拉取"。这是拿 S3 与 GitHub 两种未来实现
压测后的结论：GitHub 有 tree diff 能直接返回增量，S3 与本地目录都没有变更流，若协议
提供 list_changes(cursor)，后两者只能退化成"全量列举后自己算差异"——那这个方法就是在
骗调用方。增量因此是框架层的能力（data_source_sync.py），对所有连接器一致。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol

from .errors import AppError


class SourceObject(NamedTuple):
    """数据源里的一个对象。

    ``version`` 的契约是"内容变了才变，内容没变就不变"。不同连接器用不同东西满足它：
    本地目录用内容 SHA-256，S3 用服务端给的 ETag。``modified_at`` 只进展示层，
    不参与任何判定——同内容重新落盘会刷新时间戳，内容改动也可能不改变 size。
    """

    key: str
    version: str
    size: int
    modified_at: datetime | None


class Connector(Protocol):
    def list_objects(self) -> Iterator[SourceObject]: ...
    def fetch(self, key: str) -> bytes: ...


def validate_object_key(key: str) -> str:
    """对象键必须是相对路径且不含上跳。

    键会被用作 documents.filename 并参与 document_id 计算，放过 ``..`` 或绝对路径
    会让不同数据源的对象互相覆盖。
    """

    if not key or key.startswith("/") or Path(key).is_absolute():
        raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键必须是相对路径。", 400)
    if ".." in Path(key).parts:
        raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键不得包含上跳路径。", 400)
    return key


class LocalDirectoryConnector:
    """把一个本地目录当作数据源。

    覆盖的真实场景：挂载的 NFS 共享、企业网盘的本地同步目录、定期落盘的导出文件。

    已知代价：``list_objects`` 要读完每个文件才能算出内容哈希，成本远高于 S3 那种
    "服务端在列举响应里直接给 ETag"。协议不为此增加"便宜的预检"方法——那会把 S3 的
    特性泄进抽象。同步框架每次 sync 只调用它一次，这个成本可以接受。
    """

    def __init__(self, root: Path, include_suffixes: tuple[str, ...]):
        self.root = Path(root)
        self.include_suffixes = tuple(suffix.lower() for suffix in include_suffixes)

    def list_objects(self) -> Iterator[SourceObject]:
        if not self.root.is_dir():
            # 不能静默返回空清单：空清单会被差异计算判定为"全部删除"，
            # 那是把配置错误伪装成数据变更。
            raise AppError(
                "SOURCE_ROOT_UNAVAILABLE",
                f"数据源根目录不可用：{self.root}",
                409,
            )
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                # 跟随符号链接会引入目录环，也会让读取越出 root。
                continue
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in self.include_suffixes:
                continue
            content = path.read_bytes()
            stat = path.stat()
            yield SourceObject(
                key=validate_object_key(path.relative_to(self.root).as_posix()),
                version=hashlib.sha256(content).hexdigest(),
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )

    def fetch(self, key: str) -> bytes:
        target = self.root / validate_object_key(key)
        resolved = target.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键越出数据源根目录。", 400)
        if not resolved.is_file():
            raise AppError("SOURCE_OBJECT_MISSING", f"对象已不存在：{key}", 409)
        return resolved.read_bytes()
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_connectors.py -v`
Expected: PASS

- [ ] **Step 5: 补齐其余契约测试并通过**

追加测试：符号链接被跳过（`target.symlink_to(...)` 后不出现在清单里）、隐藏文件被跳过、`include_suffixes` 过滤生效、根目录不存在时抛 `SOURCE_ROOT_UNAVAILABLE`、`validate_object_key` 拒绝 `../x.md` 与 `/etc/passwd`、`fetch` 拒绝越出根目录的键、子目录下的键保留 `/`（`sub/a.md`）。

Run: `uv run pytest backend/tests/test_connectors.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors.py backend/tests/test_connectors.py
git commit -m "feat: 增加数据源连接器协议与本地目录实现"
```

---

## Task 2: Schema V11

**Files:**
- Create: `backend/migrations/0011_data_source_sync.sql`
- Modify: `backend/app/config.py`、`docker-compose.yml`、`docker-compose.release.yml`、`deploy/kubernetes/configmap.yaml`、`deploy/kubernetes/workloads.yaml`
- Test: `backend/tests/test_postgres_foundation.py`

**Interfaces:**
- Consumes: 无
- Produces: `source_type` 增 `local_directory`；`data_sources` 增三列；`data_source_objects` 表；`job_type` 增 `sync`；schema 版本 11；配置项 `sync_delete_threshold_percent`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_postgres_foundation.py` 追加：

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_eleven_allows_sync_jobs_and_local_directory_sources(tmp_path: Path) -> None:
    """sync 任务不带 rebuild 字段也必须能插入。

    0003 的 index_jobs_rebuild_requires_batch 写的是
    `job_type = 'index' OR (rebuild 字段非空)`，新增的 sync 会落进后半句被要求提供
    rebuild 字段。迁移能过但插入失败，所以这条约束必须同步改写。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 11
    check_schema_version(database_url, 11)

    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, is_default, created_at, updated_at)
               VALUES ('kb_default', '默认知识库', '默认知识库', true, %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES ('ds_dir', 'kb_default', 'local_directory', '手册目录',
                       '{"root": "/mnt/docs", "include_suffixes": [".md"]}', %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO index_jobs
               (index_job_id, knowledge_base_id, data_source_id, idempotency_key,
                status, job_type, created_at, updated_at)
               VALUES ('job_sync', 'kb_default', 'ds_dir', 'sync:ds_dir:1',
                       'queued', 'sync', %s, %s)""",
            (now, now),
        )
        assert connection.execute(
            "SELECT last_sync_status FROM data_sources WHERE data_source_id='ds_dir'"
        ).fetchone()[0] == "idle"

    # 同一数据源不得有两个活动 sync 任务
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """INSERT INTO index_jobs
                   (index_job_id, knowledge_base_id, data_source_id, idempotency_key,
                    status, job_type, created_at, updated_at)
                   VALUES ('job_sync2', 'kb_default', 'ds_dir', 'sync:ds_dir:2',
                           'queued', 'sync', now(), now())"""
            )
```

同时把该文件里断言 schema 为 10 的现有测试改为 11（`grep -n "== 10\|, 10)" backend/tests/test_postgres_foundation.py` 定位）。

- [ ] **Step 2: 运行确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_postgres_foundation.py -k schema_eleven -v`
Expected: FAIL，`assert apply_migrations(...) == 11` 得到 10

- [ ] **Step 3: 写迁移**

创建 `backend/migrations/0011_data_source_sync.sql`：

```sql
-- 本地目录作为数据源。不复用 'file'：那个取值的语义已被"API 上传"占用，
-- 两者同步行为相反（上传是推、目录是拉）。
ALTER TABLE data_sources DROP CONSTRAINT data_sources_source_type_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_source_type_check
    CHECK (source_type IN ('file', 'local_directory', 'object_storage', 'web', 'connector'));

-- 同步状态改为真实列。派生字段（index_jobs 的 finished_at/status）表达不了
-- "同步成功但没有任何变化"——那种情况不产生 index job，会显示为 idle，
-- 与"从未同步"无法区分。aborted 专门表示熔断中止。
ALTER TABLE data_sources
    ADD COLUMN last_sync_at timestamptz,
    ADD COLUMN last_sync_status text NOT NULL DEFAULT 'idle'
        CHECK (last_sync_status IN ('idle', 'running', 'succeeded', 'failed', 'aborted')),
    ADD COLUMN sync_failure_reason text;

-- 比对的基础：上次同步时看到的每个对象。
CREATE TABLE data_source_objects (
    data_source_id text NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    object_key text NOT NULL,
    version text NOT NULL,
    -- 首次发现时索引尚未完成，此时还没有文档记录，因此可空。
    -- 知识库归属不在这里重复记录：data_sources 已有 knowledge_base_id。
    document_id text,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_source_id, object_key)
);

ALTER TABLE index_jobs DROP CONSTRAINT index_jobs_job_type_check;
ALTER TABLE index_jobs ADD CONSTRAINT index_jobs_job_type_check
    CHECK (job_type IN ('index', 'rebuild', 'sync'));

-- 0003 的原约束是 `job_type = 'index' OR (rebuild 字段非空)`，新增的 sync 会落进
-- 后半句而被要求提供 rebuild 字段。改成只约束 rebuild 自己。
ALTER TABLE index_jobs DROP CONSTRAINT index_jobs_rebuild_requires_batch;
ALTER TABLE index_jobs ADD CONSTRAINT index_jobs_rebuild_requires_batch
    CHECK (job_type <> 'rebuild'
           OR (rebuild_batch_id IS NOT NULL AND target_chunking_version IS NOT NULL));

-- 两个 sync 任务并发跑同一数据源会重复入队索引任务，并互相覆盖 data_source_objects。
-- 放数据库而不是应用层：CLI 可能被并发调用，0003 也用同样手法处理版本级并发。
CREATE UNIQUE INDEX index_jobs_one_active_sync_idx
    ON index_jobs (data_source_id)
    WHERE job_type = 'sync' AND status IN ('queued', 'running');
```

> 上面三个约束名已在 schema 10 的实库上实测确认（`data_sources_source_type_check`、`index_jobs_job_type_check`、`index_jobs_rebuild_requires_batch`），可直接使用。

修改 `backend/app/config.py`：

```python
    required_database_schema_version: int = Field(default=11, ge=1)
```

并新增配置项（放在 `index_job_stale_seconds` 之后，保持索引相关配置聚在一起）：

```python
    # 单次同步的删除比例超过该阈值即熔断中止，防止根目录配错被当成"全部删除"。
    sync_delete_threshold_percent: int = Field(default=30, ge=1, le=100)
```

- [ ] **Step 4: 运行确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_postgres_foundation.py -v`
Expected: PASS

- [ ] **Step 5: 同步部署配置的 schema 版本**

把 `docker-compose.yml`、`docker-compose.release.yml` 里 4 处 `REQUIRED_DATABASE_SCHEMA_VERSION: "10"` 改为 `"11"`；`deploy/kubernetes/configmap.yaml` 一处、`workloads.yaml` 两处 `--required-version, "10"` 改为 `"11"`。

Run: `uv run python -m scripts.validate_kubernetes && TEST_DATABASE_URL=... uv run pytest backend/tests/test_kubernetes_manifests.py -q`
Expected: `Kubernetes 清单边界校验通过`，测试 PASS。这条校验是 V5-5 加的，专门防止这三个数字再次腐烂。

- [ ] **Step 6: 确认迁移幂等**

Run: 对同一库连续两次 `uv run python -m scripts.database_migrate apply --database-url ...`
Expected: 两次都输出 `schema 版本：11`，无报错

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/0011_data_source_sync.sql backend/app/config.py \
        docker-compose.yml docker-compose.release.yml deploy/kubernetes/ \
        backend/tests/test_postgres_foundation.py
git commit -m "feat: 增加数据源同步 Schema V11"
```

---

## Task 3: `index_document` 支持指定数据源与相对路径

**Files:**
- Modify: `backend/app/postgres_documents.py`（`PostgresAsyncRAGService.index_document`）
- Test: `backend/tests/test_pgvector_integration.py`

**Interfaces:**
- Consumes: Task 2 的 `local_directory` 取值
- Produces: `index_document(..., data_source_id: str | None = None, relative_path: str | None = None)`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_index_document_keeps_same_named_files_in_different_directories_apart(tmp_path) -> None:
    """目录树里不同子目录下的同名文件必须是两个文档。

    改造前 safe_name = Path(filename).name 会把 handbook/a/x.md 与 handbook/b/x.md
    算成同一个 document_id，后者覆盖前者。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, FakeEmbedder(), None, None)
    source_id = _create_directory_source(database_url)

    first = service.index_document(
        "x.md", b"来自 a 目录", KNOWLEDGE_BASE_ID,
        data_source_id=source_id, relative_path="a/x.md",
    )
    second = service.index_document(
        "x.md", b"来自 b 目录", KNOWLEDGE_BASE_ID,
        data_source_id=source_id, relative_path="b/x.md",
    )

    assert first.document_id != second.document_id
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            "SELECT filename, data_source_id FROM documents ORDER BY filename"
        ).fetchall()
    assert [row[0] for row in rows] == ["a/x.md", "b/x.md"]
    # 归属传入的数据源，而不是每个文件自建一个
    assert {row[1] for row in rows} == {source_id}
```

`_create_directory_source` 用 SQL 插入一条 `local_directory` 数据源并返回其 id。

- [ ] **Step 2: 运行确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_pgvector_integration.py -k same_named -v`
Expected: FAIL，`TypeError: index_document() got an unexpected keyword argument 'data_source_id'`

- [ ] **Step 3: 改实现**

`index_document` 签名加两个可选参数，函数体开头改为：

```python
        validate_knowledge_base_id(knowledge_base_id)
        # 不传两个新参数时行为与改造前完全一致：API 上传路径依赖这一点。
        safe_name = relative_path or Path(filename).name
        if relative_path is not None:
            from .connectors import validate_object_key

            safe_name = validate_object_key(relative_path)
        content_hash = hashlib.sha256(content).hexdigest()
        document_id = _stable_id("doc", knowledge_base_id, safe_name.casefold())
        source_id = data_source_id or _stable_id("src", knowledge_base_id, safe_name.casefold())
```

改造只在"传入了 data_source_id 就用它"这一点上，`"doc"` / `"src"` 两个前缀的原有逻辑不动。

并把创建 data_source 的那段 INSERT 包在条件里——传入了 `data_source_id` 说明数据源已由同步流程创建，不该再插一条：

```python
                if data_source_id is None:
                    connection.execute(
                        """INSERT INTO data_sources ... VALUES (%s, %s, 'file', %s, ...)""",
                        (source_id, knowledge_base_id, safe_name, now, now),
                    )
```

- [ ] **Step 4: 运行确认通过并验证向后兼容**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_pgvector_integration.py backend/tests/test_api.py backend/tests/test_postgres_foundation.py -q`
Expected: 全部 PASS。既有 API 上传测试全绿是这一步的硬性验收条件——它们走的就是"两个参数都不传"的路径。

- [ ] **Step 5: Commit**

```bash
git add backend/app/postgres_documents.py backend/tests/test_pgvector_integration.py
git commit -m "feat: index_document 支持指定数据源与相对路径"
```

---

## Task 4: 差异计算与熔断

**Files:**
- Create: `backend/app/data_source_sync.py`
- Test: `backend/tests/test_sync_pipeline.py`

**Interfaces:**
- Consumes: `SourceObject`（Task 1）
- Produces:
  - `SyncDiff(added: list[SourceObject], updated: list[SourceObject], deleted: list[str])`
  - `compute_diff(remote: list[SourceObject], known: dict[str, str]) -> SyncDiff`
  - `check_delete_circuit_breaker(diff: SyncDiff, known_total: int, threshold_percent: int) -> None`（触发时抛 `AppError("SYNC_DELETE_CIRCUIT_BREAKER")`）

- [ ] **Step 1: 写失败测试**

```python
def test_compute_diff_classifies_three_kinds_of_change() -> None:
    remote = [
        SourceObject("keep.md", "v1", 10, None),      # 两边一致
        SourceObject("edit.md", "v2-new", 10, None),  # version 变了
        SourceObject("new.md", "v3", 10, None),       # 本地没有
    ]
    known = {"keep.md": "v1", "edit.md": "v2-old", "gone.md": "v4"}

    diff = compute_diff(remote, known)

    assert [item.key for item in diff.added] == ["new.md"]
    assert [item.key for item in diff.updated] == ["edit.md"]
    assert diff.deleted == ["gone.md"]


def test_circuit_breaker_trips_past_threshold() -> None:
    """删除比例超阈值时中止。挡的是根目录配错被当成"全部删除"。"""

    diff = SyncDiff(added=[], updated=[], deleted=["a", "b", "c", "d"])
    with pytest.raises(AppError) as error:
        check_delete_circuit_breaker(diff, known_total=5, threshold_percent=30)
    assert error.value.code == "SYNC_DELETE_CIRCUIT_BREAKER"
    assert "a" in error.value.message


def test_circuit_breaker_skips_first_sync() -> None:
    """首次同步没有可删的东西，不做判定。"""

    diff = SyncDiff(added=[SourceObject("a.md", "v1", 1, None)], updated=[], deleted=[])
    check_delete_circuit_breaker(diff, known_total=0, threshold_percent=30)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_sync_pipeline.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.app.data_source_sync`

- [ ] **Step 3: 实现**

创建 `backend/app/data_source_sync.py`，实现三者。差异计算是纯函数（不碰数据库），熔断判定同样是纯函数——这样它们能在没有 PostgreSQL 的环境里被测到。

熔断的判定式与错误信息：

```python
def check_delete_circuit_breaker(
    diff: SyncDiff, known_total: int, threshold_percent: int
) -> None:
    """删除比例超阈值即中止，不执行任何写入。

    连新增也不执行：触发熔断的典型原因是"看到的清单不可信"（根目录被误改、挂载点掉了、
    导出任务没跑成功），此时算出的新增同样不可信。V5-5 的索引版本回滚救不了这种情况——
    那是索引层的回滚，文档记录本身的删除不在它的范围内。
    """

    if known_total == 0 or not diff.deleted:
        return
    ratio = len(diff.deleted) * 100 / known_total
    if ratio > threshold_percent:
        raise AppError(
            "SYNC_DELETE_CIRCUIT_BREAKER",
            f"待删除 {len(diff.deleted)}/{known_total} 个对象（{ratio:.0f}%），"
            f"超过阈值 {threshold_percent}%，已中止：{'、'.join(sorted(diff.deleted)[:10])}",
            409,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_sync_pipeline.py -v`
Expected: PASS（3 个纯 Python 测试）

- [ ] **Step 5: Commit**

```bash
git add backend/app/data_source_sync.py backend/tests/test_sync_pipeline.py
git commit -m "feat: 增加同步差异计算与删除熔断"
```

---

## Task 5: 同步编排与 worker 分流

**Files:**
- Modify: `backend/app/data_source_sync.py`
- Modify: `backend/app/postgres_documents.py`（`IndexWorker._process` 开头分流）
- Test: `backend/tests/test_sync_pipeline.py`

**Interfaces:**
- Consumes: Task 1/3/4
- Produces:
  - `enqueue_sync(database_url: str, data_source_id: str, max_attempts: int = 3) -> dict[str, object]`
  - `run_sync(settings: Settings, embedder, job: dict) -> dict[str, object]`（由 worker 调用）
  - `build_connector(configuration: dict) -> Connector`

- [ ] **Step 1: 写失败测试——完整同步语义**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_sync_handles_add_update_delete_and_noop(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (root / name).write_text(f"{name} 的内容" * 30, encoding="utf-8")
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url)

    # 首次同步：全部索引
    _run_full_sync(settings, database_url, source_id)
    assert _document_count(database_url) == 3
    assert _searchable_count(database_url) == 3

    # 无变化再同步：零 index job
    before = _index_job_count(database_url)
    _run_full_sync(settings, database_url, source_id)
    assert _index_job_count(database_url) == before, "无变化的同步不得产生任务"

    # touch 全部文件：仍然零 index job（version 只跟内容有关）
    for name in ("a.md", "b.md", "c.md"):
        os.utime(root / name, (0, 0))
    _run_full_sync(settings, database_url, source_id)
    assert _index_job_count(database_url) == before

    # 改一个文件：只重建那一个
    (root / "a.md").write_text("改过的内容" * 30, encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    assert _index_job_count(database_url) == before + 1

    # 删一个：软删除，检索不到但记录保留
    (root / "c.md").unlink()
    _run_full_sync(settings, database_url, source_id)
    assert _document_count(database_url) == 3
    assert _searchable_count(database_url) == 2

    # 恢复：回到可检索，且不重新索引
    (root / "c.md").write_text("c.md 的内容" * 30, encoding="utf-8")
    jobs_before_restore = _index_job_count(database_url)
    _run_full_sync(settings, database_url, source_id)
    assert _searchable_count(database_url) == 3
    assert _index_job_count(database_url) == jobs_before_restore, "内容未变的恢复不得重新索引"
```

`_run_full_sync` 封装「enqueue_sync + 跑 worker 直到队列空」。`_searchable_count` 统计 `documents.metadata->>'retrieval_status'` 为 `searchable`（或缺省）的文档数。

- [ ] **Step 2: 运行确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_sync_pipeline.py -k add_update_delete -v`
Expected: FAIL，`ImportError: cannot import name 'enqueue_sync'`

- [ ] **Step 3: 实现同步编排**

在 `data_source_sync.py` 实现 `enqueue_sync`、`build_connector`、`run_sync`。`run_sync` 按 spec 第 6 节的八步执行；软删除与恢复的实现见 Task 6，本步先留出调用点。

`enqueue_sync` 的幂等键用 `f"sync:{data_source_id}:{uuid4().hex[:12]}"`，并依赖 Task 2 的 `index_jobs_one_active_sync_idx` 拒绝并发——捕获 `UniqueViolation` 转成 `AppError("SYNC_ALREADY_RUNNING")`，与 `enqueue_rebuild` 处理 `REBUILD_IN_PROGRESS` 的手法一致。

在 `IndexWorker._process` **最开头**分流（原实现第一件事就是查 `document_version_id` 对应的版本，sync 任务没有这个字段，会直接 `RuntimeError`）：

```python
    def _process(self, job: dict[str, Any]) -> None:
        if str(job.get("job_type", "index")) == "sync":
            # sync 任务针对整个数据源，没有 document_version_id，不能走下面的版本查询。
            run_sync(self.settings, self.embedder, job)
            return
```

- [ ] **Step 4: 运行确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_sync_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: 补中断续跑与熔断的数据库级测试**

追加两个测试：

- **中断续跑**：首次同步入队后只跑一个 index job 就停，再次 `enqueue_sync` + 跑完，断言最终文档数正确且已完成对象没有被重复索引（靠 `data_source_objects.version` 已写入而跳过）。
- **熔断**：3 个文件删掉 2 个（67% > 30%）后同步，断言数据源 `last_sync_status = 'aborted'`、`sync_failure_reason` 含待删清单、且 `_searchable_count` 与 `_document_count` **都没有变化**。

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_sync_pipeline.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/data_source_sync.py backend/app/postgres_documents.py backend/tests/test_sync_pipeline.py
git commit -m "feat: 增加数据源同步编排与 Worker 分流"
```

---

## Task 6: 软删除与自动恢复

**Files:**
- Modify: `backend/app/data_source_sync.py`
- Test: `backend/tests/test_retrieval_access.py`

**Interfaces:**
- Consumes: Task 5 的调用点
- Produces:
  - `mark_documents_deleted(database_url: str, knowledge_base_id: str, document_ids: list[str]) -> int`
  - `mark_documents_searchable(database_url: str, knowledge_base_id: str, document_ids: list[str]) -> int`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_soft_deleted_documents_leave_retrieval_but_keep_chunks(tmp_path) -> None:
    """软删除只让分块不可检索，文档、版本与向量全部保留。

    retrieval_status 的 'deleted' 取值 V5-3 就预留在 schemas.py 的 Literal 里，
    检索侧 retrieval_access.py:32 已经在挡非 searchable 的分块。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        frontend_origin="http://localhost:5173",
    )
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    indexed = service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, _FakeEmbedder()).run_once()
    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID)
    chunks_before = _chunk_count(database_url)

    marked = mark_documents_deleted(database_url, KNOWLEDGE_BASE_ID, [indexed.document_id])

    assert marked == 1
    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID) == []
    assert _chunk_count(database_url) == chunks_before, "软删除不得删除分块"

    restored = mark_documents_searchable(database_url, KNOWLEDGE_BASE_ID, [indexed.document_id])

    assert restored == 1
    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID)
```

- [ ] **Step 2: 运行确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_retrieval_access.py -k soft_deleted -v`
Expected: FAIL，`ImportError`

- [ ] **Step 3: 实现**

两个函数都写 `documents.metadata` 与分块 metadata。**分块侧必须覆盖 `active` / `previous` / `building` 三种状态的索引版本**——照 V5-5 第 8.2 节的规则，参考 `postgres_repositories.py` 里 ACL 写扩散那两处已经改好的 SQL 形状（`FROM documents d, index_versions iv ... AND iv.status IN ('active','previous','building')`）。

只更新 active 版本会造成：切回 previous 之后被软删除的文档重新可检索。

- [ ] **Step 4: 运行确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_retrieval_access.py -v`
Expected: PASS

- [ ] **Step 5: 加回滚场景的判别性测试**

追加：软删除后，手工把 `previous` 版本切为 `active`（复用 `test_retrieval_access.py` 已有的 `_simulate_rollback` 辅助），断言被软删的文档**仍然**检索不到。这条测的正是"只刷 active 版本"这个错误实现会漏掉的场景。

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_retrieval_access.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/data_source_sync.py backend/tests/test_retrieval_access.py
git commit -m "feat: 增加同步软删除与自动恢复"
```

---

## Task 7: CLI、只读接口与文档

**Files:**
- Create: `scripts/sync_data_source.py`
- Modify: `backend/app/postgres_repositories.py`（数据源列表返回真实同步状态列）
- Modify: `README.md`、`docs/operations/postgres-migration-recovery.md`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 5 的 `enqueue_sync`
- Produces: CLI 四个子命令；数据源列表接口返回 `last_sync_at` / `last_sync_status` / `sync_failure_reason`

- [ ] **Step 1: 写失败测试**

`PostgresDataSourceRepository.list` 当前把 `last_synced_at` / `sync_status` 从 `index_jobs` 派生（`j.finished_at AS last_synced_at, j.status AS sync_status`）。改为读真实列。测试断言：同步成功但无变化时，状态是 `succeeded` 而不是 `idle`——这正是派生字段表达不了的情形。

- [ ] **Step 2: 运行确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_api.py -k data_source -v`
Expected: FAIL

- [ ] **Step 3: 实现 CLI 与接口改造**

`scripts/sync_data_source.py` 照 `scripts/switch_index.py` 的形状写（同样的 `database_url()` 解析、`check_schema_version`、`_print` JSON 输出、`AppError` 透出错误码）：

- `create --knowledge-base --name --root [--suffixes]`：插入一条 `local_directory` 数据源
- `list --knowledge-base`：列出数据源与同步状态
- `sync --data-source`：`enqueue_sync`，提示需要 Worker 消费
- `status --data-source`：读同步状态与最近失败原因

`PostgresDataSourceRepository.list` 的 SQL 把派生的 `last_synced_at` / `sync_status` 换成真实列，同时保留 `upload_status`（那个仍然是从 index_jobs 派生的，语义不同，不动）。

- [ ] **Step 4: 运行确认通过**

Run: `uv run ruff check backend evaluations scripts && TEST_DATABASE_URL=... uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: 更新文档**

`README.md` 新增「本地目录同步」小节：CLI 命令序列、增量判定基于内容哈希（`touch` 不会触发重新索引）、删除是软删除、熔断阈值与含义、**外部数据源接入属于 V5-7 而非本阶段**。`docs/operations/postgres-migration-recovery.md` 增加同步失败与熔断中止的处置步骤（按错误码分类：`SOURCE_ROOT_UNAVAILABLE` 检查挂载、`SYNC_DELETE_CIRCUIT_BREAKER` 核对根目录配置后再决定是否放行、`SYNC_ALREADY_RUNNING` 等前一次跑完）。

不得把「支持数据同步」写成「支持企业数据源」，也不得把 `object_storage` / `web` / `connector` 三个枚举值宣传为已实现。

- [ ] **Step 6: 全量质量门**

Run:
```bash
uv run ruff check backend evaluations scripts
TEST_DATABASE_URL=... uv run pytest -q
uv run python -m scripts.validate_kubernetes
source ~/.nvm/nvm.sh && nvm exec 20.20.2 npm --prefix frontend test -- --run
nvm exec 20.20.2 npm --prefix frontend run lint && nvm exec 20.20.2 npm --prefix frontend run build
```
Expected: 全部通过。前端未改动，仍需运行以确认没有连带破坏。**注意 Node 必须 ≥20.19.0**，低于该版本 npm 会静默跳过 rolldown 的平台 binding，测试与构建报 `Cannot find native binding` 且退出码仍是 0。

- [ ] **Step 7: 端到端演练**

按 README 新增小节实际跑一遍：目录放 5 个文档 → 首次同步全部索引 → 不改动再同步产生零 index job → `touch` 全部文件再同步仍然零 job → 改一个 → 只重建一个 → 删两个 → 软删两个且检索不到 → 恢复一个 → 检索恢复 → 删掉 4/5 触发熔断且数据库无变更 → `root` 指向不存在路径得到 `SOURCE_ROOT_UNAVAILABLE`。把实际输出附在 PR 描述里。

- [ ] **Step 8: Commit**

```bash
git add scripts/sync_data_source.py backend/app/postgres_repositories.py \
        backend/tests/test_api.py README.md docs/operations/postgres-migration-recovery.md
git commit -m "feat: 增加数据源同步 CLI 与只读同步状态"
```

---

## 计划自查结论

**Spec 覆盖**：spec 第 3 节 → Task 1；第 4 节 → Task 1；第 5 节 → Task 2；第 6 节 → Task 5；第 6.1 节 → Task 3；第 7 节 → Task 4（熔断）+ Task 6（软删除）；第 8 节（零依赖）→ Task 1 只用标准库；第 9 节 → Task 7；第 10 节测试表 → 各任务的测试步骤；第 11 节验收 → Task 7 Step 6/7。第 2 节非目标不产生任务。

**执行时必须先读源文件确认、不得凭本计划字面量照抄的地方**：

- `PostgresDataSourceRepository.list` 的完整 SQL 形状（Task 7）。
- `test_retrieval_access.py` 的 `_simulate_rollback` 与 `_statuses_carrying_deny` 辅助函数签名（Task 6 Step 5）。

**顺序依赖**：Task 1 → 3 → 4 → 5 → 6 → 7，其中 Task 2 必须在 3 之前（`local_directory` 取值和新表）。Task 4 的纯函数测试不依赖数据库，可与 Task 2/3 并行。Task 5 的软删除调用点在 Task 6 完成前留空，因此 Task 5 的删除相关断言要等 Task 6 才能全绿——这一点在 Task 5 Step 1 的测试里已经包含，执行时若先做 Task 5 可临时 `xfail` 删除与恢复两条断言，Task 6 完成后移除标记。
