# V5-5 索引版本切换与回滚 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库索引以全库粒度产生可并存的版本，切换由固定数据集质量门放行，并可原子回滚。

**Architecture:** `chunks` 增加 `index_version_id` 维度，新表 `index_versions` 记录每个版本冻结的解析/切分/向量配置与放行报告，`knowledge_bases.active_index_version_id` 作为唯一读指针。重建写入非 active 版本因此对用户不可见，切换与回滚都是单事务移动指针。每个版本建一个部分 HNSW 索引，避开 pgvector 的 post-filter 召回缺陷。

**Tech Stack:** PostgreSQL 16 + pgvector、psycopg 3、Pydantic v2、pytest、Ruff。

**Spec:** `docs/design/v5-5-index-version-switch.md`

## Global Constraints

- Schema 版本推进到 10：新增迁移必须命名 `backend/migrations/0010_index_versions.sql`，编号连续（`backend/app/database.py:14` 会校验），且 `Settings.required_database_schema_version` 同步改为 `10`。
- 迁移在单事务内执行（`database.py:44`），禁止使用 `CREATE INDEX CONCURRENTLY`。
- 迁移文件一旦提交不得再修改内容：`schema_migrations.checksum` 会拒绝变更（`database.py:41`）。开发期改动需重建测试库。
- 向量维度不写死数字，一律从 `index_settings.embedding_dimension` 或运行时 `len(embeddings[0])` 取得。
- 所有 PostgreSQL 测试加 `@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")`。
- 审计元数据受 `backend/app/audit.py:18` 的 `_SAFE_METADATA_KEYS` 白名单限制，本阶段不扩白名单：索引版本 id 放 `resource_id`，`resource_type` 用 `"index_version"`。
- 中文注释与中文 commit message，技术标识保留英文（AGENTS.md）。
- 不实现前端页面，不移除 Chroma 运行时，不做自动过期清理。

---

## File Structure

| 文件 | 责任 |
| --- | --- |
| `backend/migrations/0010_index_versions.sql`（新建） | 建 `index_versions`、加 `chunks.index_version_id`、加 `knowledge_bases.active_index_version_id`、存量回填、固定向量维度 |
| `backend/app/index_versions.py`（新建） | 索引版本的配置指纹、创建、状态转换、切换/回滚/清理、部分索引 DDL。独立成模块因为 `postgres_documents.py` 已 980 行 |
| `backend/app/postgres_documents.py`（改） | 重建入队创建 building 版本；worker 写 `index_version_id` 且不再删旧分块；5 处读路径加 active 过滤 |
| `backend/app/postgres_repositories.py`（改） | 2 处 ACL/分类写扩散改为覆盖所有非 retired 版本 |
| `backend/app/config.py`（改） | `required_database_schema_version` 改 10 |
| `backend/evaluation/report.py`（改） | `RetrievalEvaluationReport` 增可选 `config_fingerprint` |
| `backend/evaluation/run_corpus_baseline.py`（改） | 生成报告时写入配置指纹 |
| `scripts/switch_index.py`（新建） | `prepare / status / switch / rollback / retire` CLI |
| `backend/app/main.py`（改） | `GET /api/knowledge-bases/{id}/index-versions`，仅管理员 |
| `backend/tests/test_index_versions.py`（新建） | 状态机约束、切换/回滚原子性、质量门拒绝路径 |

---

## Task 1: Schema V10 迁移与维度固定

**Files:**
- Create: `backend/migrations/0010_index_versions.sql`
- Modify: `backend/app/config.py:24`
- Test: `backend/tests/test_postgres_foundation.py`

**Interfaces:**
- Consumes: 无
- Produces: 表 `index_versions`、列 `chunks.index_version_id`、列 `knowledge_bases.active_index_version_id`；schema 版本 10

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_postgres_foundation.py` 末尾追加：

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_v10_backfills_active_index_version():
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    # 先应用到 V9，插入一条历史分块，再应用 V10，验证回填
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        assert int(version) == 10
        row = connection.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='chunks' AND column_name='index_version_id'"""
        ).fetchone()
        assert row is not None
        row = connection.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='knowledge_bases' AND column_name='active_index_version_id'"""
        ).fetchone()
        assert row is not None
```

同时把该文件中断言 schema 版本为 9 的现有测试改为 10（用 `grep -n "== 9" backend/tests/test_postgres_foundation.py` 定位）。

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_postgres_foundation.py -k schema_v10 -v`
Expected: FAIL，`assert int(version) == 10` 得到 9

- [ ] **Step 3: 写迁移**

创建 `backend/migrations/0010_index_versions.sql`：

```sql
-- 索引版本是知识库级的读指针来源：重建写入非 active 版本，用户检索因此看不到未放行的分块。
CREATE TABLE index_versions (
    index_version_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE RESTRICT,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'active', 'previous', 'retired', 'failed')),
    chunking_version text NOT NULL,
    parser_version text NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimension integer NOT NULL CHECK (embedding_dimension > 0),
    processing_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    config_fingerprint text NOT NULL CHECK (config_fingerprint ~ '^[a-f0-9]{64}$'),
    evaluation_report_id text,
    rebuild_batch_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    retired_at timestamptz
);

-- 三种在用状态各自唯一，由数据库保证，不依赖应用层自律。
CREATE UNIQUE INDEX index_versions_one_active_idx
    ON index_versions (knowledge_base_id) WHERE status = 'active';
CREATE UNIQUE INDEX index_versions_one_building_idx
    ON index_versions (knowledge_base_id) WHERE status = 'building';
CREATE UNIQUE INDEX index_versions_one_previous_idx
    ON index_versions (knowledge_base_id) WHERE status = 'previous';

-- active 版本必须有放行报告；building/ready 阶段还没有。
ALTER TABLE index_versions ADD CONSTRAINT index_versions_active_requires_report
    CHECK (status <> 'active' OR evaluation_report_id IS NOT NULL);

ALTER TABLE knowledge_bases
    ADD COLUMN active_index_version_id text REFERENCES index_versions(index_version_id);

ALTER TABLE chunks ADD COLUMN index_version_id text REFERENCES index_versions(index_version_id);

-- 存量回填：每个已有知识库得到一条 active 版本。配置取自现有事实，取不到写 legacy 而不猜测。
DO $$
DECLARE
    kb record;
    new_id text;
    settings record;
BEGIN
    SELECT embedding_model, embedding_dimension INTO settings FROM index_settings WHERE singleton;
    FOR kb IN SELECT knowledge_base_id FROM knowledge_bases LOOP
        IF NOT EXISTS (SELECT 1 FROM chunks WHERE knowledge_base_id = kb.knowledge_base_id) THEN
            CONTINUE;
        END IF;
        new_id := 'iv_' || substr(md5(kb.knowledge_base_id || clock_timestamp()::text), 1, 16);
        INSERT INTO index_versions (
            index_version_id, knowledge_base_id, status, chunking_version, parser_version,
            embedding_model, embedding_dimension, processing_options, config_fingerprint,
            evaluation_report_id, activated_at
        )
        SELECT new_id, kb.knowledge_base_id, 'active',
               COALESCE(max(v.chunking_version), 'legacy'),
               COALESCE(max(v.parser_version), 'legacy'),
               COALESCE(settings.embedding_model, 'legacy'),
               COALESCE(settings.embedding_dimension, 1),
               '{"legacy": true}'::jsonb,
               md5(new_id) || md5('legacy-backfill'),
               'legacy-backfill', now()
        FROM documents d
        JOIN document_versions v ON v.document_version_id = d.current_version_id
        WHERE d.knowledge_base_id = kb.knowledge_base_id;

        UPDATE chunks SET index_version_id = new_id WHERE knowledge_base_id = kb.knowledge_base_id;
        UPDATE knowledge_bases SET active_index_version_id = new_id
            WHERE knowledge_base_id = kb.knowledge_base_id;
    END LOOP;
END $$;

-- 回填完成后才能加 NOT NULL；空库没有分块，约束同样成立。
ALTER TABLE chunks ALTER COLUMN index_version_id SET NOT NULL;

ALTER TABLE chunks DROP CONSTRAINT chunks_document_version_id_chunk_index_key;
ALTER TABLE chunks ADD CONSTRAINT chunks_version_index_key
    UNIQUE (document_version_id, index_version_id, chunk_index);

CREATE INDEX chunks_index_version_idx ON chunks (index_version_id);

-- pgvector 的 HNSW/IVFFlat 要求列带维度修饰，无维度列会报
-- "column does not have dimensions"。维度从既有登记读取，不写死数字；
-- 空库时 index_settings 无行，保持无维度，由首次 register_embedding_model 补做。
DO $$
DECLARE dim integer;
BEGIN
    SELECT embedding_dimension INTO dim FROM index_settings WHERE singleton;
    IF dim IS NOT NULL THEN
        EXECUTE format('ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(%s)', dim);
    END IF;
END $$;
```

修改 `backend/app/config.py:24`：

```python
    required_database_schema_version: int = Field(default=10, ge=1)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_postgres_foundation.py -v`
Expected: PASS。若 `chunks_document_version_id_chunk_index_key` 名称不符，先用
`SELECT conname FROM pg_constraint WHERE conrelid='chunks'::regclass` 查实际名并改迁移。

- [ ] **Step 5: 确认迁移幂等**

Run: 对同一库连续执行两次 `uv run python -m scripts.database_migrate apply --database-url ...`
Expected: 第二次输出 `schema 版本：10`，无报错（`schema_migrations` 已记录则跳过）。

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0010_index_versions.sql backend/app/config.py backend/tests/test_postgres_foundation.py
git commit -m "feat: 增加索引版本 Schema V10 与存量回填"
```

---

## Task 2: 索引版本模块与配置指纹

**Files:**
- Create: `backend/app/index_versions.py`
- Test: `backend/tests/test_index_versions.py`（新建）

**Interfaces:**
- Consumes: Task 1 的表结构
- Produces:
  - `config_fingerprint(chunking_version: str, parser_version: str, embedding_model: str, embedding_dimension: int, processing_options: dict) -> str`
  - `create_building_version(database_url: str, knowledge_base_id: str, *, chunking_version: str, parser_version: str, embedding_model: str, embedding_dimension: int, processing_options: dict, rebuild_batch_id: str) -> str`
  - `active_index_version_id(database_url: str, knowledge_base_id: str) -> str | None`
  - `get_version(database_url: str, index_version_id: str) -> dict[str, Any] | None`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_index_versions.py`：

```python
from __future__ import annotations

import os

import psycopg
import pytest

from backend.app.database import apply_migrations
from backend.app.index_versions import (
    active_index_version_id,
    config_fingerprint,
    create_building_version,
)

KNOWLEDGE_BASE_ID = "kb_default"


def test_config_fingerprint_is_stable_and_order_independent():
    first = config_fingerprint("v1-700-100", "structured-1", "test/embedding", 3, {"a": 1, "b": 2})
    second = config_fingerprint("v1-700-100", "structured-1", "test/embedding", 3, {"b": 2, "a": 1})
    assert first == second
    assert len(first) == 64


def test_config_fingerprint_changes_with_chunking_version():
    first = config_fingerprint("v1-700-100", "structured-1", "test/embedding", 3, {})
    second = config_fingerprint("v1-160-20", "structured-1", "test/embedding", 3, {})
    assert first != second
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest backend/tests/test_index_versions.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.app.index_versions`

- [ ] **Step 3: 写实现**

创建 `backend/app/index_versions.py`：

```python
"""索引版本的配置指纹、创建与状态查询。

索引版本承载"这批分块由什么配置产出"这一事实，切换放行时用配置指纹比对评测报告，
阻止用一套配置的合格报告去放行另一套配置的索引。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def config_fingerprint(
    chunking_version: str,
    parser_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
) -> str:
    """按规范化 JSON 计算指纹，键顺序不影响结果。"""

    canonical = json.dumps(
        {
            "chunking_version": chunking_version,
            "parser_version": parser_version,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "processing_options": processing_options,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_building_version(
    database_url: str,
    knowledge_base_id: str,
    *,
    chunking_version: str,
    parser_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
    rebuild_batch_id: str,
) -> str:
    index_version_id = f"iv_{uuid4().hex[:16]}"
    fingerprint = config_fingerprint(
        chunking_version, parser_version, embedding_model, embedding_dimension, processing_options
    )
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                rebuild_batch_id)
               VALUES (%s, %s, 'building', %s, %s, %s, %s, %s, %s, %s)""",
            (
                index_version_id,
                knowledge_base_id,
                chunking_version,
                parser_version,
                embedding_model,
                embedding_dimension,
                Jsonb(processing_options),
                fingerprint,
                rebuild_batch_id,
            ),
        )
    return index_version_id


def active_index_version_id(database_url: str, knowledge_base_id: str) -> str | None:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_version(database_url: str, index_version_id: str) -> dict[str, Any] | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM index_versions WHERE index_version_id = %s", (index_version_id,)
        ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest backend/tests/test_index_versions.py -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: 加数据库侧约束测试并通过**

追加到 `backend/tests/test_index_versions.py`：

```python
def _reset(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, description, is_default,
                created_at, updated_at)
               VALUES (%s, '默认知识库', '默认知识库', '', true, now(), now())""",
            (KNOWLEDGE_BASE_ID,),
        )


def _create(database_url: str, chunking: str = "v1-700-100") -> str:
    return create_building_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version=chunking,
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=3,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        rebuild_batch_id="rbd_test",
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_only_one_building_version_per_knowledge_base():
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    _create(database_url)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _create(database_url, "v1-160-20")


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_active_version_requires_evaluation_report():
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE index_versions SET status='active' WHERE index_version_id=%s",
                (index_version_id,),
            )
```

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -v`
Expected: PASS（4 个测试）

- [ ] **Step 6: Commit**

```bash
git add backend/app/index_versions.py backend/tests/test_index_versions.py
git commit -m "feat: 增加索引版本配置指纹与创建接口"
```

---

## Task 3: 重建入队创建 building 版本

**Files:**
- Modify: `backend/app/postgres_documents.py:596-660`（`enqueue_rebuild`）
- Test: `backend/tests/test_index_rebuild.py`

**Interfaces:**
- Consumes: `create_building_version`（Task 2）
- Produces: `enqueue_rebuild(...)` 返回值增加 `index_version_id` 键；入队范围改为 KB 全量当前版本

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_index_rebuild.py` 追加：

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_enqueue_rebuild_creates_building_index_version(tmp_path):
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("profile.md", DOCUMENT_TEXT.encode("utf-8"), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, FakeEmbedder()).run_once()

    result = enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, chunking_version(160, 20))

    assert result["index_version_id"].startswith("iv_")
    # 已用目标配置的文档也要入队：全库级切换要求新版本覆盖全量文档
    assert result["queued"] == 1
    with psycopg.connect(database_url) as connection:
        status = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s",
            (result["index_version_id"],),
        ).fetchone()[0]
    assert status == "building"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_rebuild.py -k building_index_version -v`
Expected: FAIL，`KeyError: 'index_version_id'`

- [ ] **Step 3: 改实现**

在 `backend/app/postgres_documents.py` 顶部 import 区加：

```python
from .index_versions import create_building_version
```

把 `enqueue_rebuild` 的 docstring 与候选查询改为（替换 `:602-630` 对应片段）：

```python
    """为知识库建立一个新的 building 索引版本，并把全部当前版本排队重建。

    与 V5-4 之前的行为不同：不再只挑 ``chunking_version`` 与目标不同的文档。全库级
    切换要求新索引版本覆盖全量文档，漏一篇即新版本不完整、不能放行。
    已有排队或运行中任务的版本会被跳过，重复调用因此安全，中断后再次调用即可续跑。
    """

    validate_knowledge_base_id(knowledge_base_id)
    _, chunk_size, chunk_overlap = parse_chunking_version(target_chunking_version)
    batch_id = f"rbd_{uuid4().hex[:16]}"
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            if not connection.execute(
                "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id = %s",
                (knowledge_base_id,),
            ).fetchone():
                raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
            settings_row = connection.execute(
                "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
            ).fetchone()
            if settings_row is None:
                raise AppError(
                    "INDEX_NOT_INITIALIZED", "索引尚未登记向量模型，请先完成一次索引。", 409
                )
            candidates = connection.execute(
                """SELECT v.document_version_id, d.data_source_id, v.parser_version
                   FROM documents d
                   JOIN document_versions v ON v.document_version_id = d.current_version_id
                   WHERE d.knowledge_base_id = %s
                     AND NOT EXISTS (
                         SELECT 1 FROM index_jobs j
                         WHERE j.document_version_id = v.document_version_id
                           AND j.status IN ('queued', 'running'))
                   ORDER BY d.document_id""",
                (knowledge_base_id, ),
            ).fetchall()
```

在同一函数内，创建索引版本后再入队（`queued = 0` 之前插入）：

```python
    index_version_id = create_building_version(
        database_url,
        knowledge_base_id,
        chunking_version=target_chunking_version,
        parser_version=str(candidates[0]["parser_version"]) if candidates else "structured-1",
        embedding_model=str(settings_row["embedding_model"]),
        embedding_dimension=int(settings_row["embedding_dimension"]),
        processing_options={"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
        rebuild_batch_id=batch_id,
    )
```

返回值加入新键：

```python
    return {
        "batch_id": batch_id,
        "index_version_id": index_version_id,
        "knowledge_base_id": knowledge_base_id,
        "target_chunking_version": target_chunking_version,
        "queued": queued,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_rebuild.py -v`
Expected: 新测试 PASS。既有断言"只重建配置不同的文档"的测试会失败——按新语义更新它们的期望值（`queued` 现在等于 KB 当前版本文档数）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/postgres_documents.py backend/tests/test_index_rebuild.py
git commit -m "feat: 重建入队时建立 building 索引版本"
```

---

## Task 4: Worker 写入索引版本且保留旧分块

**Files:**
- Modify: `backend/app/postgres_documents.py:760-948`（`IndexWorker._process`）
- Test: `backend/tests/test_index_rebuild.py`

**Interfaces:**
- Consumes: Task 3 的 `index_jobs.rebuild_batch_id` → 索引版本映射
- Produces: `chunks.index_version_id` 被正确写入；重建不再删除旧分块

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rebuild_keeps_previous_chunks_and_hides_building_version(tmp_path):
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("profile.md", DOCUMENT_TEXT.encode("utf-8"), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, FakeEmbedder()).run_once()
    original = _chunk_count(database_url)

    enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, chunking_version(160, 20))
    fine = _settings(tmp_path, database_url, 160, 20)
    while IndexWorker(fine, FakeEmbedder()).run_once():
        pass

    # 旧分块必须仍在库中，否则回滚无从谈起
    assert _chunk_count(database_url) > original
    # 用户检索只看到 active 版本，行数与重建前一致
    visible = service.load_current_chunks(KNOWLEDGE_BASE_ID)
    assert len(visible) == original
```

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_rebuild.py -k keeps_previous_chunks -v`
Expected: FAIL，`_chunk_count` 未增长（旧分块被 `DELETE` 掉了）

- [ ] **Step 3: 改实现**

在 `_process` 读取 job 后解析目标索引版本（`target_version` 计算之后插入）：

```python
        # 重建任务的分块归属入队时创建的索引版本；普通索引任务归当前 active 版本。
        if str(job.get("job_type", "index")) == "rebuild":
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    "SELECT index_version_id FROM index_versions WHERE rebuild_batch_id = %s",
                    (job["rebuild_batch_id"],),
                ).fetchone()
            if row is None:
                raise RuntimeError("rebuild batch has no index version")
            index_version_id = str(row[0])
        else:
            index_version_id = _active_or_bootstrap_index_version(
                self.database_url,
                str(version["knowledge_base_id"]),
                target_version,
                parsed_parser_version="structured-1",
                embedder_model=self.embedder.model_name,
            )
```

把写入分块的事务块改为（替换 `:870-890`）：

```python
                # 不再 DELETE 同版本旧分块：旧索引版本必须完整保留，回滚才成立。
                # 同一 (document_version_id, index_version_id) 的重复执行由唯一约束兜底，
                # 重试时先删本索引版本自己的分块。
                connection.execute(
                    """DELETE FROM chunks
                       WHERE document_version_id = %s AND index_version_id = %s""",
                    (version["document_version_id"], index_version_id),
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True):
                    connection.execute(
                        """INSERT INTO chunks
                           (chunk_id, document_version_id, index_version_id, knowledge_base_id,
                            chunk_index, content, metadata, embedding, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            f"{version['document_version_id']}:{index_version_id[3:11]}:{chunk.chunk_index:05d}",
                            version["document_version_id"],
                            index_version_id,
                            version["knowledge_base_id"],
                            chunk.chunk_index,
                            chunk.text,
                            Jsonb(chunk.metadata()),
                            embedding,
                            now,
                        ),
                    )
```

删除 rebuild 分支里回写 `document_versions.chunking_version` 等字段的语句（原 `:893-908`），改为只更新解析事实：

```python
                if str(job.get("job_type", "index")) == "rebuild":
                    # 切分与向量配置现在归索引版本管理，document_versions 只记录解析结果。
                    connection.execute(
                        """UPDATE document_versions SET parser_name=%s, parser_version=%s,
                                  parsed_content_hash=%s, parse_status='ready',
                                  parse_failure_code=NULL, parsed_tree=%s
                           WHERE document_version_id=%s""",
                        (
                            parsed.parser_name,
                            parsed.parser_version,
                            hashlib.sha256(content).hexdigest(),
                            Jsonb(parsed.tree_payload()),
                            version["document_version_id"],
                        ),
                    )
```

在模块内新增引导函数（普通索引任务在 active 版本不存在时创建首个版本）：

```python
def _active_or_bootstrap_index_version(
    database_url: str,
    knowledge_base_id: str,
    chunking_version_value: str,
    parsed_parser_version: str,
    embedder_model: str,
) -> str:
    """普通索引任务写入 active 版本；首次索引时创建并直接激活第一个版本。

    首个版本没有可比较的前序基线，因此不要求评测报告，用固定标记满足约束。
    """

    from .index_versions import active_index_version_id, config_fingerprint

    existing = active_index_version_id(database_url, knowledge_base_id)
    if existing:
        return existing
    index_version_id = f"iv_{uuid4().hex[:16]}"
    with psycopg.connect(database_url) as connection, connection.transaction():
        row = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
        ).fetchone()
        model = str(row[0]) if row else embedder_model
        dimension = int(row[1]) if row else 1
        _, chunk_size, chunk_overlap = parse_chunking_version(chunking_version_value)
        options = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id, activated_at)
               VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s, 'initial-index', now())""",
            (
                index_version_id,
                knowledge_base_id,
                chunking_version_value,
                parsed_parser_version,
                model,
                dimension,
                Jsonb(options),
                config_fingerprint(
                    chunking_version_value, parsed_parser_version, model, dimension, options
                ),
            ),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
            (index_version_id, knowledge_base_id),
        )
    return index_version_id
```

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_rebuild.py -v`
Expected: PASS。此步依赖 Task 6 的读路径过滤才能让 `load_current_chunks` 断言成立——若先行执行本任务，该断言允许暂时标记 `xfail`，并在 Task 6 移除标记。

- [ ] **Step 5: Commit**

```bash
git add backend/app/postgres_documents.py backend/tests/test_index_rebuild.py
git commit -m "feat: 重建写入独立索引版本并保留旧分块"
```

---

## Task 5: 批次完成判定与部分 HNSW 索引

**Files:**
- Modify: `backend/app/index_versions.py`
- Modify: `backend/app/postgres_documents.py`（`rebuild_status`）
- Test: `backend/tests/test_index_versions.py`

**Interfaces:**
- Consumes: Task 2/3/4
- Produces:
  - `finalize_building_version(database_url: str, index_version_id: str) -> str`（返回新状态 `ready` 或 `failed`）
  - `create_partial_vector_index(database_url: str, index_version_id: str) -> None`
  - `drop_partial_vector_index(database_url: str, index_version_id: str) -> None`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_requires_full_document_coverage():
    from backend.app.index_versions import finalize_building_version
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    # 没有任何分块写入，覆盖不完整
    assert finalize_building_version(database_url, index_version_id) == "failed"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -k finalize -v`
Expected: FAIL，`ImportError: cannot import name 'finalize_building_version'`

- [ ] **Step 3: 写实现**

追加到 `backend/app/index_versions.py`：

```python
def finalize_building_version(database_url: str, index_version_id: str) -> str:
    """按覆盖完整性把 building 版本推进到 ready 或 failed。

    分母是"有 current_version_id 的文档数"：尚未成功索引的 pending / failed 文档
    本就没有可用分块，把它们计入会让新版本永远无法放行。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            version = connection.execute(
                """SELECT knowledge_base_id, status, rebuild_batch_id
                   FROM index_versions WHERE index_version_id = %s FOR UPDATE""",
                (index_version_id,),
            ).fetchone()
            if version is None:
                raise ValueError(f"index version not found: {index_version_id}")
            if str(version["status"]) != "building":
                return str(version["status"])
            unfinished = connection.execute(
                """SELECT count(*) AS total FROM index_jobs
                   WHERE rebuild_batch_id = %s AND status IN ('queued', 'running')""",
                (version["rebuild_batch_id"],),
            ).fetchone()
            if int(unfinished["total"]) > 0:
                return "building"
            failed = connection.execute(
                """SELECT count(*) AS total FROM index_jobs
                   WHERE rebuild_batch_id = %s AND status IN ('failed', 'cancelled')""",
                (version["rebuild_batch_id"],),
            ).fetchone()
            expected = connection.execute(
                """SELECT count(*) AS total FROM documents
                   WHERE knowledge_base_id = %s AND current_version_id IS NOT NULL""",
                (version["knowledge_base_id"],),
            ).fetchone()
            covered = connection.execute(
                """SELECT count(DISTINCT document_version_id) AS total FROM chunks
                   WHERE index_version_id = %s""",
                (index_version_id,),
            ).fetchone()
            complete = int(failed["total"]) == 0 and int(covered["total"]) == int(expected["total"])
            status = "ready" if complete else "failed"
            connection.execute(
                "UPDATE index_versions SET status=%s WHERE index_version_id=%s",
                (status, index_version_id),
            )
    if status == "ready":
        create_partial_vector_index(database_url, index_version_id)
    return status


def _partial_index_name(index_version_id: str) -> str:
    return f"chunks_hnsw_{index_version_id.replace('-', '_')}"


def create_partial_vector_index(database_url: str, index_version_id: str) -> None:
    """为单个索引版本建部分 HNSW 索引。

    pgvector 对带 WHERE 过滤的 ANN 查询是 post-filter，默认只取 ef_search 个候选再过滤，
    会静默少返回。索引版本只有极少取值，官方对这种场景推荐部分索引：索引内只含本版本的行，
    过滤条件与索引条件一致，post-filter 问题因此不出现。
    """

    name = _partial_index_name(index_version_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            f"""CREATE INDEX IF NOT EXISTS {name} ON chunks
                USING hnsw (embedding vector_cosine_ops)
                WHERE index_version_id = %s""",
            (index_version_id,),
        )


def drop_partial_vector_index(database_url: str, index_version_id: str) -> None:
    name = _partial_index_name(index_version_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP INDEX IF EXISTS {name}")
```

在 `postgres_documents.py` 的 `rebuild_status` 返回值中加入索引版本状态：查出该 batch 的
`index_version_id` 与 `status` 并加进返回字典，同时调用 `finalize_building_version`，
使 `status` 命令本身推进状态机。

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -v`
Expected: PASS

- [ ] **Step 5: 验证部分索引真被使用**

追加测试：

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_partial_index_is_used_by_planner(tmp_path):
    from backend.app.index_versions import create_partial_vector_index
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    # 需要先有固定维度的 embedding 列与足够分块，沿用 test_index_rebuild 的建库流程
    # 建索引后用 EXPLAIN 确认走 Index Scan
    ...
```

该测试的建库步骤直接复用 `backend/tests/test_index_rebuild.py` 的 `_settings` / `_service`
/ `IndexWorker` 流程（导入即可）；断言：

```python
    with psycopg.connect(database_url) as connection:
        plan = connection.execute(
            """EXPLAIN SELECT chunk_id FROM chunks
               WHERE index_version_id = %s
               ORDER BY embedding <=> %s::vector LIMIT 5""",
            (index_version_id, [0.1, 0.2, 0.3]),
        ).fetchall()
    assert any("Index Scan" in str(row[0]) for row in plan)
```

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -k planner -v`
Expected: PASS。若 PostgreSQL 因数据量太小选择顺扫，在该测试内 `SET enable_seqscan = off`
后再 EXPLAIN，并在注释里说明原因。

- [ ] **Step 6: Commit**

```bash
git add backend/app/index_versions.py backend/app/postgres_documents.py backend/tests/test_index_versions.py
git commit -m "feat: 增加索引版本完成判定与部分向量索引"
```

---

## Task 6: 读路径按 active 索引版本过滤

**Files:**
- Modify: `backend/app/postgres_documents.py:92-240`（`query`、`load_current_chunks`、`chunk_fingerprint`、`score_by_ids`、`list_documents`）
- Test: `backend/tests/test_hybrid_retrieval.py`、`backend/tests/test_index_rebuild.py`

**Interfaces:**
- Consumes: `active_index_version_id`（Task 2）
- Produces: 5 处 SQL 增加 `AND c.index_version_id = %s`；`chunk_fingerprint` 返回值含 active 版本 id

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_hybrid_retrieval.py` 追加：

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_chunk_fingerprint_includes_active_index_version(tmp_path):
    database_url = os.environ["TEST_DATABASE_URL"]
    # 建库、索引一篇文档（复用本文件既有夹具），记录指纹
    before = service.chunk_fingerprint(KNOWLEDGE_BASE_ID)
    # 手工把 active 指针换成另一个版本
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
            (other_index_version_id, KNOWLEDGE_BASE_ID),
        )
    assert service.chunk_fingerprint(KNOWLEDGE_BASE_ID) != before
```

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_hybrid_retrieval.py -k fingerprint -v`
Expected: FAIL，指纹不变（切换后 BM25 倒排不会失效，混合检索会命中已切走的分块）

- [ ] **Step 3: 改实现**

在 `PostgresAsyncRAGService`（或 store 类）内加一个私有取值方法，单次请求内复用：

```python
    def _active_index_version(self, knowledge_base_id: str) -> str:
        from .index_versions import active_index_version_id

        current = active_index_version_id(self.database_url, knowledge_base_id)
        if current is None:
            raise AppError("INDEX_NOT_INITIALIZED", "知识库尚无可用索引版本。", 409)
        return current
```

五处 SQL 各加一个条件与参数：

- `:101` `query`：JOIN 条件后加 `AND c.index_version_id = %s`
- `:147` `load_current_chunks`：`WHERE` 内加 `AND c.index_version_id = %s`
- `:185` `chunk_fingerprint`：同上，并把 active 版本 id 拼进返回字符串：
  ```python
      return f"{active}:{int(row[0])}:{row[1].isoformat()}:{int(row[2])}:{int(row[3])}:{row[4]}"
  ```
- `:191` `score_by_ids`：`WHERE` 内加 `AND index_version_id = %s`
- `:229` `list_documents`：把 `LEFT JOIN chunks c ON c.document_version_id = d.current_version_id`
  改为 `LEFT JOIN chunks c ON c.document_version_id = d.current_version_id AND c.index_version_id = %s`，
  否则并存期间 `chunk_count` 翻倍

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_hybrid_retrieval.py backend/tests/test_index_rebuild.py backend/tests/test_retrieval_access.py -v`
Expected: PASS，并移除 Task 4 Step 4 里可能添加的 `xfail` 标记

- [ ] **Step 5: Commit**

```bash
git add backend/app/postgres_documents.py backend/tests/test_hybrid_retrieval.py backend/tests/test_index_rebuild.py
git commit -m "feat: 检索读路径按 active 索引版本过滤"
```

---

## Task 7: ACL 与分类变更覆盖所有非 retired 版本

**Files:**
- Modify: `backend/app/postgres_repositories.py:292-296`、`:469-474`
- Test: `backend/tests/test_retrieval_access.py`

**Interfaces:**
- Consumes: Task 1 的 `chunks.index_version_id`、`index_versions.status`
- Produces: 两处 `UPDATE chunks SET metadata` 覆盖 `active` / `previous` / `building` 三种状态

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_acl_tightening_survives_rollback(tmp_path):
    """ACL 收紧后回滚到上一索引版本，被拒用户仍然检索不到。

    若 ACL 只刷进 active 版本的分块，回滚会把收紧前的宽松 ACL 一起带回来，构成越权。
    """
    database_url = os.environ["TEST_DATABASE_URL"]
    # 1. 建库、索引文档、完成一次重建并切换，使库中同时存在 active 与 previous 版本
    # 2. 对 data_source 执行 ACL 收紧，deny 掉 user_denied
    # 3. 回滚到 previous 版本
    # 4. 以 user_denied 身份检索，断言拿不到任何分块
    candidates = service.retrieve_candidates(
        "备份目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID,
        access=RetrievalAccessContext(user_id="user_denied", is_admin=False),
    )
    assert candidates == []
```

（`RetrievalAccessContext` 的实际字段以 `backend/app/retrieval_access.py` 为准，编写时先读该文件。）

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_retrieval_access.py -k survives_rollback -v`
Expected: FAIL，回滚后仍能检索到分块

- [ ] **Step 3: 改实现**

`postgres_repositories.py:292-296` 的 ACL 扩散语句改为：

```python
                connection.execute(
                    """UPDATE chunks c SET metadata=c.metadata || %s
                       FROM documents d
                       JOIN index_versions iv ON iv.index_version_id = c.index_version_id
                       WHERE d.data_source_id=%s
                         AND c.knowledge_base_id=d.knowledge_base_id
                         AND c.document_version_id=d.current_version_id
                         AND iv.status IN ('active', 'previous', 'building')""",
                    (Jsonb({"data_source_acl": policy}), data_source_id),
                )
```

`:469-474` 的分类扩散语句同样加 `JOIN index_versions` 与状态条件，去掉隐含的"只改 active"语义。

> 注意：`retired` 与 `failed` 版本不更新——它们只等清理，写入无意义。

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_retrieval_access.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/postgres_repositories.py backend/tests/test_retrieval_access.py
git commit -m "fix: ACL 与分类变更覆盖所有在用索引版本"
```

---

## Task 8: 评测报告记录配置指纹

**Files:**
- Modify: `backend/evaluation/report.py:37-60`
- Modify: `backend/evaluation/run_corpus_baseline.py`
- Test: `backend/tests/test_corpus_evaluation.py`

**Interfaces:**
- Consumes: `config_fingerprint`（Task 2）
- Produces: `RetrievalEvaluationReport.config_fingerprint: str | None`

- [ ] **Step 1: 写失败测试**

```python
def test_report_accepts_optional_config_fingerprint():
    from backend.evaluation.report import RetrievalEvaluationReport
    payload = _minimal_report_payload()  # 复用本文件既有构造助手
    payload["config_fingerprint"] = "a" * 64
    report = RetrievalEvaluationReport(**payload)
    assert report.config_fingerprint == "a" * 64


def test_legacy_report_without_fingerprint_still_loads():
    from backend.evaluation.report import RetrievalEvaluationReport
    report = RetrievalEvaluationReport(**_minimal_report_payload())
    assert report.config_fingerprint is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest backend/tests/test_corpus_evaluation.py -k fingerprint -v`
Expected: FAIL，`config_fingerprint` 字段不存在（Pydantic 忽略或报错）

- [ ] **Step 3: 改实现**

在 `RetrievalEvaluationReport` 内，紧随 `rerank_recall_at_5` 之后添加：

```python
    # 1.0.0 的历史报告没有这一项，保持可选以免旧报告失效；缺该项的报告不能用于放行索引切换。
    config_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
```

在 `run_corpus_baseline.py` 构造报告处，用被测配置计算并传入：

```python
    from backend.app.index_versions import config_fingerprint as compute_fingerprint

    fingerprint = compute_fingerprint(
        chunking_version(chunk_size, chunk_overlap),
        "structured-1",
        resolved_model(embedder),
        len(embedder.encode(["维度探测"])[0]),
        {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
    )
```

并把 `config_fingerprint=fingerprint` 加入 `RetrievalEvaluationReport(...)` 调用。
`parser_version` 取值必须与 Task 3 写入索引版本时一致，否则指纹永不匹配——两处都用
`backend/app/parsers.py` 暴露的解析器版本常量，编写时先读该文件确认标识名。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest backend/tests/test_corpus_evaluation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/evaluation/report.py backend/evaluation/run_corpus_baseline.py backend/tests/test_corpus_evaluation.py
git commit -m "feat: 评测报告记录索引配置指纹"
```

---

## Task 9: 切换、回滚与清理

**Files:**
- Modify: `backend/app/index_versions.py`
- Test: `backend/tests/test_index_versions.py`

**Interfaces:**
- Consumes: Task 2/5/8
- Produces:
  - `switch_to_version(database_url: str, index_version_id: str, report: RetrievalEvaluationReport, audit: AuditRepository | None = None) -> dict[str, str]`
  - `rollback_to_previous(database_url: str, knowledge_base_id: str, audit: AuditRepository | None = None) -> dict[str, str]`
  - `retire_version(database_url: str, index_version_id: str) -> int`（返回删除的分块数）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_fingerprint_mismatch():
    from backend.app.index_versions import switch_to_version
    from backend.app.errors import AppError
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='ready' WHERE index_version_id=%s",
            (index_version_id,),
        )
    report = _passing_report(fingerprint="b" * 64)  # 与该版本指纹不同
    with pytest.raises(AppError) as error:
        switch_to_version(database_url, index_version_id, report)
    assert error.value.code == "INDEX_CONFIG_MISMATCH"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_report_without_fingerprint():
    from backend.app.index_versions import switch_to_version
    from backend.app.errors import AppError
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='ready' WHERE index_version_id=%s",
            (index_version_id,),
        )
    with pytest.raises(AppError) as error:
        switch_to_version(database_url, index_version_id, _passing_report(fingerprint=None))
    assert error.value.code == "INDEX_REPORT_INCOMPLETE"
```

`_passing_report` 构造一份 `official=True`、三项指标 `passed=True` 的
`RetrievalEvaluationReport`，指纹可控。

- [ ] **Step 2: 运行测试确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -k switch_rejects -v`
Expected: FAIL，`ImportError: cannot import name 'switch_to_version'`

- [ ] **Step 3: 写实现**

追加到 `backend/app/index_versions.py`：

```python
def switch_to_version(
    database_url: str,
    index_version_id: str,
    report: "RetrievalEvaluationReport",
    audit: "AuditRepository | None" = None,
) -> dict[str, str]:
    """把 ready 版本切为 active，原 active 降为 previous，原 previous 转 retired。

    指标是否回退由报告自己的 ``passed`` 决定——生成报告时把当前 active 版本的指标
    作为 baseline 传入即可，这里不重复实现比较规则（见 evaluation/report.py 的 assess_metric）。
    """

    from .errors import AppError

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            target = connection.execute(
                "SELECT * FROM index_versions WHERE index_version_id=%s FOR UPDATE",
                (index_version_id,),
            ).fetchone()
            if target is None:
                raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
            if str(target["status"]) != "ready":
                raise AppError(
                    "INDEX_VERSION_NOT_READY",
                    f"索引版本状态为 {target['status']}，只有 ready 可以切换。",
                    409,
                )
            if not report.official:
                raise AppError("INDEX_REPORT_NOT_OFFICIAL", "放行报告必须是正式报告。", 409)
            if not report.passed:
                raise AppError("INDEX_QUALITY_REGRESSED", "评测指标未达阈值或相对基线回退。", 409)
            if report.config_fingerprint is None:
                raise AppError(
                    "INDEX_REPORT_INCOMPLETE",
                    "放行报告缺少配置指纹，无法证明它评测的就是该索引版本的配置。",
                    409,
                )
            if report.config_fingerprint != str(target["config_fingerprint"]):
                raise AppError(
                    "INDEX_CONFIG_MISMATCH",
                    "报告的配置指纹与索引版本不一致，拒绝放行。",
                    409,
                )
            knowledge_base_id = str(target["knowledge_base_id"])
            connection.execute(
                """UPDATE index_versions SET status='retired', retired_at=now()
                   WHERE knowledge_base_id=%s AND status='previous'""",
                (knowledge_base_id,),
            )
            previous = connection.execute(
                """UPDATE index_versions SET status='previous'
                   WHERE knowledge_base_id=%s AND status='active'
                   RETURNING index_version_id""",
                (knowledge_base_id,),
            ).fetchone()
            connection.execute(
                """UPDATE index_versions
                   SET status='active', activated_at=now(), evaluation_report_id=%s
                   WHERE index_version_id=%s""",
                (report.report_id, index_version_id),
            )
            connection.execute(
                "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
                (index_version_id, knowledge_base_id),
            )
    result = {
        "knowledge_base_id": knowledge_base_id,
        "active": index_version_id,
        "previous": str(previous["index_version_id"]) if previous else "",
    }
    if audit is not None:
        audit.record(
            "index_version.activate",
            actor_id=None,
            actor_role="operator",
            resource_type="index_version",
            resource_id=index_version_id,
            result="success",
        )
    return result


def rollback_to_previous(
    database_url: str,
    knowledge_base_id: str,
    audit: "AuditRepository | None" = None,
) -> dict[str, str]:
    """把 previous 切回 active，原 active 降为 previous。回滚不需要新报告：
    目标版本此前已由质量门放行过，其 evaluation_report_id 仍然有效。"""

    from .errors import AppError

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            target = connection.execute(
                """SELECT * FROM index_versions
                   WHERE knowledge_base_id=%s AND status='previous' FOR UPDATE""",
                (knowledge_base_id,),
            ).fetchone()
            if target is None:
                raise AppError("INDEX_NO_PREVIOUS_VERSION", "没有可回滚的上一索引版本。", 409)
            demoted = connection.execute(
                """UPDATE index_versions SET status='ready'
                   WHERE knowledge_base_id=%s AND status='active'
                   RETURNING index_version_id""",
                (knowledge_base_id,),
            ).fetchone()
            connection.execute(
                "UPDATE index_versions SET status='active', activated_at=now() WHERE index_version_id=%s",
                (target["index_version_id"],),
            )
            connection.execute(
                "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
                (target["index_version_id"], knowledge_base_id),
            )
    result = {
        "knowledge_base_id": knowledge_base_id,
        "active": str(target["index_version_id"]),
        "demoted": str(demoted["index_version_id"]) if demoted else "",
    }
    if audit is not None:
        audit.record(
            "index_version.rollback",
            actor_id=None,
            actor_role="operator",
            resource_type="index_version",
            resource_id=str(target["index_version_id"]),
            result="success",
        )
    return result


def retire_version(database_url: str, index_version_id: str) -> int:
    """删除 retired / failed 版本的分块与其部分索引。状态本身不代表数据已删除。"""

    from .errors import AppError

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            version = connection.execute(
                "SELECT status FROM index_versions WHERE index_version_id=%s FOR UPDATE",
                (index_version_id,),
            ).fetchone()
            if version is None:
                raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
            if str(version["status"]) not in {"retired", "failed"}:
                raise AppError(
                    "INDEX_VERSION_IN_USE",
                    f"索引版本状态为 {version['status']}，只有 retired 或 failed 可以清理。",
                    409,
                )
            deleted = connection.execute(
                "DELETE FROM chunks WHERE index_version_id=%s", (index_version_id,)
            ).rowcount
    drop_partial_vector_index(database_url, index_version_id)
    return int(deleted)
```

顶部补 `TYPE_CHECKING` import 以支持类型标注：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audit import AuditRepository
    from ..evaluation.report import RetrievalEvaluationReport
```

- [ ] **Step 4: 运行测试确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -v`
Expected: PASS

- [ ] **Step 5: 补切换与回滚的正向测试**

追加：切换成功后 `knowledge_bases.active_index_version_id` 指向新版本、旧版本状态为
`previous` 且其分块行数未变；回滚后指针回到旧版本；`retire_version` 对 `active` 版本抛
`INDEX_VERSION_IN_USE`。

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_index_versions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/index_versions.py backend/tests/test_index_versions.py
git commit -m "feat: 增加索引版本切换、回滚与清理"
```

---

## Task 10: CLI、只读 API 与文档

**Files:**
- Create: `scripts/switch_index.py`
- Modify: `backend/app/main.py`
- Modify: `README.md`
- Modify: `docs/operations/postgres-migration-recovery.md`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 9 的三个函数、Task 5 的 `finalize_building_version`
- Produces: CLI 五个子命令；`GET /api/knowledge-bases/{id}/index-versions`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_api.py` 追加（用 `FakeService` 路径，不依赖真实库）：

```python
def test_index_versions_requires_admin(client):
    response = client.get("/api/knowledge-bases/kb_default/index-versions")
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)
```

若 `FakeService` 无对应能力，为 `conftest.py` 的 `FakeService` 增加
`list_index_versions(knowledge_base_id)` 返回空列表，与其它 Fake 方法风格一致。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest backend/tests/test_api.py -k index_versions -v`
Expected: FAIL，404

- [ ] **Step 3: 写实现**

创建 `scripts/switch_index.py`：

```python
"""管理知识库索引版本的切换与回滚。

切换必须提供固定数据集评测报告；报告的配置指纹与目标索引版本不一致时拒绝执行。
质量门评的是"该配置在冻结语料上不回退"，不代表验证了生产数据的检索质量。
"""

from __future__ import annotations

import argparse
import json
import os

from backend.app.audit import AuditRepository
from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.index_versions import (
    rollback_to_previous,
    retire_version,
    switch_to_version,
)
from backend.app.postgres_documents import rebuild_status
from backend.evaluation.report import RetrievalEvaluationReport


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "status", "switch", "rollback", "retire"))
    parser.add_argument("--database-url")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--index-version")
    parser.add_argument("--batch")
    parser.add_argument("--report")
    args = parser.parse_args()

    settings = get_settings()
    url = database_url(args.database_url)
    check_schema_version(url, settings.required_database_schema_version)
    audit = AuditRepository(settings.audit_path)

    if args.command == "status":
        if not args.batch:
            raise SystemExit("status 需要 --batch")
        print(json.dumps(rebuild_status(url, args.batch), ensure_ascii=False, indent=2))
        return

    if args.command == "switch":
        if not args.index_version or not args.report:
            raise SystemExit("switch 需要 --index-version 与 --report")
        report = RetrievalEvaluationReport.model_validate_json(
            open(args.report, encoding="utf-8").read()
        )
        print(json.dumps(
            switch_to_version(url, args.index_version, report, audit),
            ensure_ascii=False, indent=2,
        ))
        return

    if args.command == "rollback":
        if not args.knowledge_base:
            raise SystemExit("rollback 需要 --knowledge-base")
        print(json.dumps(
            rollback_to_previous(url, args.knowledge_base, audit),
            ensure_ascii=False, indent=2,
        ))
        return

    if args.command == "retire":
        if not args.index_version:
            raise SystemExit("retire 需要 --index-version")
        print(json.dumps(
            {"deleted_chunks": retire_version(url, args.index_version)},
            ensure_ascii=False, indent=2,
        ))
        return

    raise SystemExit("prepare 由 scripts/rebuild_index.py start 承担，此处不重复实现")
```

> `prepare` 明确指向既有 `rebuild_index.py start`，不新造第二个入口。

在 `backend/app/main.py` 增加只读路由，权限沿用文件中既有的管理员依赖（编写时先
`grep -n "require_admin\|AdminDependency" backend/app/main.py` 确认实际名称）：

```python
@app.get("/api/knowledge-bases/{knowledge_base_id}/index-versions")
def list_index_versions(knowledge_base_id: str, service: ServiceDependency, admin=AdminDependency):
    return {"items": service.list_index_versions(knowledge_base_id)}
```

并在 `PostgresAsyncRAGService` 与 `RAGService` 上实现 `list_index_versions`：
PostgreSQL 版查 `index_versions` 表并按 `created_at DESC` 返回；Chroma 版返回空列表
（该运行时没有索引版本概念）。同步在 `RAGServiceProtocol` 加签名。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest backend/tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: 更新文档**

在 `README.md` 的检索章节后新增"索引版本切换与回滚"小节，说明：完整演练命令序列、
质量门口径边界（只覆盖冻结语料，不代表生产数据质量）、`previous` 版本不会自动清理、
迁移建索引会锁表。在 `docs/operations/postgres-migration-recovery.md` 增加切换失败与
回滚的处置步骤。API 表格增加 `GET /api/knowledge-bases/{id}/index-versions` 一行。

- [ ] **Step 6: 全量质量门**

Run:
```bash
uv run ruff check backend evaluations scripts
TEST_DATABASE_URL=... uv run pytest
cd frontend && npm test && npm run lint && npm run build
```
Expected: 全部通过。前端未改动，仍需运行以确认没有连带破坏。

- [ ] **Step 7: 端到端演练**

按 README 新增小节实际执行一遍：建立新索引版本 → 重建全量 → 隔离评测 → 切换 →
检索命中新版本 → 回滚 → 检索命中旧版本，并确认 `data/audit/events.json` 出现
`index_version.activate` 与 `index_version.rollback` 两条事件。把实际输出附在 PR 描述里。

- [ ] **Step 8: Commit**

```bash
git add scripts/switch_index.py backend/app/main.py backend/app/service.py backend/tests/test_api.py backend/tests/conftest.py README.md docs/operations/postgres-migration-recovery.md
git commit -m "feat: 增加索引版本切换 CLI 与只读接口"
```

---

## 计划自查结论

**Spec 覆盖**：设计文档 4/5/6/7/8/9/10 节分别由 Task 1、Task 5、Task 3+4、Task 8+9、
Task 6+7、Task 10、各任务的测试步骤覆盖。第 2 节（pgvector 约束）体现在 Task 1 的维度
ALTER 与 Task 5 的部分索引。第 3 节非目标不产生任务。第 11 节验收由 Task 10 Step 6/7 覆盖。

**已知待确认项**（执行时先读源文件确认，不要凭名字推断）：
- `chunks` 原唯一约束的实际名称（Task 1 Step 4 已给查询命令）。
- `RetrievalAccessContext` 的字段（Task 7 Step 1）。
- `backend/app/parsers.py` 暴露的解析器版本标识名（Task 8 Step 3）。
- `main.py` 中管理员依赖的实际名称（Task 10 Step 3）。

**顺序依赖**：Task 4 的一个断言依赖 Task 6，已在 Task 4 Step 4 标注处理方式。其余任务
按编号顺序执行即可，每个任务结束时代码可运行、测试可通过。
