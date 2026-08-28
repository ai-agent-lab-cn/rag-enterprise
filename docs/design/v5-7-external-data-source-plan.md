# 外部数据源接入（S3 兼容对象存储）实施计划

> 状态说明：本文是最初的连接器专项计划。V5-7 已按固定 8 步扩展到 Schema V12、
> Sync Run 治理 API、知识库详情数据源页面和运维闭环；以
> `docs/design/v5-7-multi-source-management.md` 为当前实施范围。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库能从 S3 兼容对象存储增量同步，并用这个第二实现检验 V5-6 的连接器协议。

**Architecture:** 复用 V5-6 的同步框架，只新增一个连接器实现。`version` 用服务端返回的 ETag，契约随之收紧为单向保证。超限对象在列举阶段按 `size` 跳过，不下载。

**Tech Stack:** PostgreSQL 16 + pgvector、psycopg 3、`minio` SDK 7.2.20、pytest、Ruff。CI 增加 MinIO service。

**Spec:** `docs/design/v5-7-external-data-source.md`

## Global Constraints

- **不需要新迁移。** `object_storage` 取值在 `0001` 就有、`0011` 也保留了（已核实），schema 停在 11，`required_database_schema_version` 不动。
- **不改协议接口形状**：`Connector` 仍只有 `list_objects` 与 `fetch`。只改 `SourceObject.version` 的契约文字。
- 大小上限**复用 `max_upload_mb`**（`config.py:37`，默认 15、上限 100），不新增配置项。
- 超限对象**记为跳过而非失败**：不入队、不软删、不影响熔断分母。
- 本地目录与 S3 两个实现对同一份配置必须有相同行为——size 跳过要在两边都做。
- 所有需要 MinIO 的测试加 `@pytest.mark.skipif(not os.getenv("MINIO_ENDPOINT"), reason="需要 MinIO")`，与既有 `TEST_DATABASE_URL` 的处理方式一致。
- 凭据只从环境变量读，**绝不写进 `configuration`**。
- 不做分批同步（spec 第 9 节非目标）。
- 中文注释与中文 commit message，技术标识保留英文（AGENTS.md）。

---

## File Structure

| 文件 | 责任 |
| --- | --- |
| `backend/app/connectors.py`（改） | 收紧 `version` 契约文字；本地目录加 size 跳过；新增 `S3Connector` 与错误映射 |
| `backend/app/data_source_sync.py`（改） | `build_connector` 按 `source_type` 分派；把 `max_upload_mb` 传给连接器；同步结果报告跳过清单 |
| `pyproject.toml`（改） | 引入 `minio` |
| `.github/workflows/pytest.yml`（改） | 增加 minio service 与环境变量 |
| `backend/tests/test_connectors.py`（扩） | 两个连接器的契约，含 S3 错误映射与超限跳过 |
| `backend/tests/test_s3_sync.py`（新建） | S3 端到端同步，需 MinIO |
| `scripts/sync_data_source.py`（改） | `create` 支持创建 `object_storage` 数据源 |

---

## Task 1: 收紧 version 契约并给本地目录加 size 跳过

先做这一步而不是先写 S3：它是 V5-6 的补正，改的是既有实现，做完之后 S3 只需照着同一套语义写。

**Files:**
- Modify: `backend/app/connectors.py`
- Test: `backend/tests/test_connectors.py`

**Interfaces:**
- Consumes: 无
- Produces: `LocalDirectoryConnector(root, include_suffixes, max_bytes: int | None = None)`；实例属性 `skipped: list[tuple[str, int]]`

- [ ] **Step 1: 写失败测试**

```python
def test_oversized_files_are_skipped_without_reading(tmp_path: Path) -> None:
    """超限文件必须在读取之前就被跳过。

    fetch 与 list_objects 都把整个文件读进内存，桶里或目录里一个大文件足以打死 Worker。
    过滤必须发生在 read_bytes 之前，用 stat 的大小判断。
    """

    (tmp_path / "small.md").write_text("小文件", encoding="utf-8")
    (tmp_path / "huge.md").write_bytes(b"x" * 5000)
    connector = LocalDirectoryConnector(tmp_path, (".md",), max_bytes=1000)

    keys = [item.key for item in connector.list_objects()]

    assert keys == ["small.md"]
    assert connector.skipped == [("huge.md", 5000)]


def test_no_limit_when_max_bytes_is_none(tmp_path: Path) -> None:
    (tmp_path / "huge.md").write_bytes(b"x" * 5000)

    keys = [item.key for item in LocalDirectoryConnector(tmp_path, (".md",)).list_objects()]

    assert keys == ["huge.md"]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_connectors.py -k oversized -v`
Expected: FAIL，`TypeError: __init__() got an unexpected keyword argument 'max_bytes'`

- [ ] **Step 3: 改实现**

`LocalDirectoryConnector.__init__` 增加 `max_bytes: int | None = None`，并初始化 `self.skipped: list[tuple[str, int]] = []`。

`list_objects` 开头清空 `self.skipped`（同一实例可能被调用多次），并在**读文件之前**判定大小：

```python
            size = path.stat().st_size
            if self.max_bytes is not None and size > self.max_bytes:
                # 必须在 read_bytes 之前判定：否则大文件已经进内存了，跳过也来不及。
                self.skipped.append((path.relative_to(self.root).as_posix(), size))
                continue
            content = path.read_bytes()
```

同时把 `SourceObject.version` 的契约文字改为单向保证：

```python
    """数据源里的一个对象。

    ``version`` 的契约是「**内容变了，version 一定变**」。反向不保证——version 变了
    内容未必变，取决于连接器：本地目录用内容 SHA-256，满足双向；S3 用服务端 ETag，
    而 ETag 在分段上传时是分段配置的函数，同一份内容换个 part_size 重传就会变（实测）。

    接受这个方向的误判是因为它安全：多做一次索引只浪费算力，结果正确；而致命的那半
    ——内容变了却没被发现，导致检索到过期内容——不会发生。

    ``modified_at`` 只进展示层，不参与任何判定。
    """
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_connectors.py -v`
Expected: PASS，既有用例全绿（`max_bytes` 默认 None 时行为不变）

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors.py backend/tests/test_connectors.py
git commit -m "feat: 收紧 version 契约并跳过超限文件"
```

---

## Task 2: S3 连接器

**Files:**
- Modify: `backend/app/connectors.py`、`pyproject.toml`
- Test: `backend/tests/test_connectors.py`

**Interfaces:**
- Consumes: Task 1 的 `skipped` 约定
- Produces: `S3Connector(endpoint, bucket, prefix, access_key, secret_key, *, region=None, secure=True, max_bytes=None)`

- [ ] **Step 1: 写失败测试（需 MinIO）**

```python
@pytest.mark.skipif(not os.getenv("MINIO_ENDPOINT"), reason="需要 MinIO")
def test_s3_uses_etag_as_version_and_strips_prefix(minio_bucket) -> None:
    """ETag 直接用作 version，key 去掉 prefix 后保留子目录。"""

    connector = _s3_connector(minio_bucket, prefix="handbook/")
    objects = {item.key: item for item in connector.list_objects()}

    assert set(objects) == {"policy.md", "sub/onboarding.md"}
    assert objects["policy.md"].version == _etag_of(minio_bucket, "handbook/policy.md")


@pytest.mark.skipif(not os.getenv("MINIO_ENDPOINT"), reason="需要 MinIO")
def test_multipart_upload_changes_etag_for_identical_content(minio_bucket) -> None:
    """同一内容换 part_size 重传，ETag 变化——这是接受 ETag 作为 version 的已知代价。

    固化它是为了防止后人把这个行为当成 bug「修」掉。
    """

    content = b"x" * (12 * 1024 * 1024)
    _put(minio_bucket, "handbook/big-a.md", content, part_size=5 * 1024 * 1024)
    _put(minio_bucket, "handbook/big-b.md", content, part_size=10 * 1024 * 1024)

    objects = {item.key: item.version for item in _s3_connector(minio_bucket, "handbook/").list_objects()}

    assert objects["big-a.md"] != objects["big-b.md"]
    assert objects["big-a.md"].endswith("-3")  # 12MB / 5MB = 3 段
```

- [ ] **Step 2: 运行确认失败**

Run: `MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest backend/tests/test_connectors.py -k s3 -v`
Expected: FAIL，`ImportError: cannot import name 'S3Connector'`

- [ ] **Step 3: 引入依赖并实现**

`pyproject.toml` 的 dependencies 增加 `"minio>=7.2.20"`，然后 `uv sync --dev`。

```python
class S3Connector:
    """把一个 S3 兼容存储桶的某个前缀当作数据源。

    ``version`` 用服务端返回的 ETag。minio SDK 已经剥离了引号（实测返回
    ``'40820467919c684a8c89388304bcd584-3'``），不需要自己处理。带 ``-N`` 后缀的是分段
    上传的复合校验值，不是内容 MD5——契约收紧为单向保证正是因为它（见 SourceObject）。

    与本地目录的成本差异很大：这里一次列举 API 调用就带回全部 ETag，不需要读对象内容。
    协议不为此增加「便宜的预检」方法，因为调用方每轮同步只列举一次。
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        prefix: str,
        access_key: str,
        secret_key: str,
        *,
        region: str | None = None,
        secure: bool = True,
        max_bytes: int | None = None,
    ):
        self.bucket = bucket
        self.prefix = prefix
        self.max_bytes = max_bytes
        self.skipped: list[tuple[str, int]] = []
        self._client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key,
            region=region, secure=secure,
        )

    def list_objects(self) -> Iterator[SourceObject]:
        self.skipped = []
        try:
            for item in self._client.list_objects(
                self.bucket, prefix=self.prefix or None, recursive=True
            ):
                if item.is_dir:
                    continue
                key = validate_object_key(_strip_prefix(str(item.object_name), self.prefix))
                size = int(item.size or 0)
                if self.max_bytes is not None and size > self.max_bytes:
                    # 列举响应已带回 size，超限对象根本不需要下载。
                    self.skipped.append((key, size))
                    continue
                yield SourceObject(
                    key=key,
                    version=str(item.etag),
                    size=size,
                    modified_at=item.last_modified,
                )
        except S3Error as error:
            raise _map_s3_error(error) from error

    def fetch(self, key: str) -> bytes:
        object_name = f"{self.prefix}{validate_object_key(key)}"
        try:
            response = self._client.get_object(self.bucket, object_name)
        except S3Error as error:
            raise _map_s3_error(error) from error
        try:
            return response.read()
        finally:
            # minio 的 get_object 返回 HTTP 响应而非字节，不释放会泄漏连接池。
            response.close()
            response.release_conn()
```

错误映射（四个 code 均已实测确认）：

```python
_S3_ERROR_CODES = {
    "NoSuchBucket": ("SOURCE_ROOT_UNAVAILABLE", "数据源存储桶不存在或不可访问。", 409),
    "InvalidAccessKeyId": ("SOURCE_CREDENTIALS_INVALID", "数据源访问凭据无效。", 409),
    "SignatureDoesNotMatch": ("SOURCE_CREDENTIALS_INVALID", "数据源访问凭据无效。", 409),
    "NoSuchKey": ("SOURCE_OBJECT_MISSING", "对象已不存在。", 409),
}


def _map_s3_error(error: S3Error) -> AppError:
    """把 S3 错误映射为项目的稳定错误码。

    ``NoSuchBucket`` 复用 ``SOURCE_ROOT_UNAVAILABLE`` 是有意的：它与本地目录的根目录
    不存在同义，同步框架已经据此拒绝「把不可达当成全部删除」，S3 侧不该另造语义。
    """

    code, message, status = _S3_ERROR_CODES.get(
        str(error.code), ("SOURCE_UNAVAILABLE", f"数据源不可访问：{error.code}", 502)
    )
    return AppError(code, message, status)
```

- [ ] **Step 4: 运行确认通过**

Run: `MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest backend/tests/test_connectors.py -v`
Expected: PASS

- [ ] **Step 5: 补齐错误映射与超限跳过的测试**

追加：桶不存在 → `SOURCE_ROOT_UNAVAILABLE`；凭据错误 → `SOURCE_CREDENTIALS_INVALID`；`fetch` 缺失对象 → `SOURCE_OBJECT_MISSING`；超限对象在列举阶段被跳过并进 `skipped`，且 `fetch` 从未被调用（用计数或桩验证）。

Run: `MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest backend/tests/test_connectors.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors.py backend/tests/test_connectors.py pyproject.toml uv.lock
git commit -m "feat: 增加 S3 兼容对象存储连接器"
```

---

## Task 3: 同步框架接入 S3 与跳过清单

**Files:**
- Modify: `backend/app/data_source_sync.py`、`scripts/sync_data_source.py`
- Test: `backend/tests/test_sync_pipeline.py`

**Interfaces:**
- Consumes: Task 1/2 的连接器
- Produces: `build_connector(configuration, source_type, max_bytes)`；`run_sync` 结果含 `skipped`

- [ ] **Step 1: 写失败测试**

本地目录侧即可验证跳过清单进入同步结果，不需要 MinIO：

```python
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_oversized_objects_never_enter_the_diff(tmp_path: Path) -> None:
    """超限对象不入队、不软删、不影响熔断分母，同步整体仍然成功。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "ok.md").write_text("# ok\n\n正文。" * 20, encoding="utf-8")
    (root / "huge.md").write_bytes(b"x" * (20 * 1024 * 1024))
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url).model_copy(update={"max_upload_mb": 1})

    _run_full_sync(settings, database_url, source_id)

    assert _document_count(database_url) == 1
    assert _sync_state(database_url, source_id)[0] == "succeeded"
    with psycopg.connect(database_url) as connection:
        keys = [
            row[0]
            for row in connection.execute(
                "SELECT object_key FROM data_source_objects ORDER BY object_key"
            ).fetchall()
        ]
    assert keys == ["ok.md"], "超限对象不得进入对象记录"
```

- [ ] **Step 2: 运行确认失败**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_sync_pipeline.py -k oversized -v`
Expected: FAIL，`huge.md` 被索引或进了对象记录

- [ ] **Step 3: 改实现**

`build_connector` 改为按 `source_type` 分派，并把大小上限传下去：

```python
def build_connector(
    configuration: dict[str, Any], source_type: str, max_bytes: int | None = None
) -> Connector:
    """按数据源类型构造连接器。

    只认已实现的类型。``web`` / ``connector`` 两个 source_type 自 0001 起就是预留值，
    不对应任何实现——把它们当已实现会让同步静默什么都不做。
    """

    if source_type == "local_directory":
        ...
    if source_type == "object_storage":
        access_key, secret_key = _read_credentials(configuration)
        ...
    raise AppError("SOURCE_TYPE_NOT_SUPPORTED", f"数据源类型 {source_type} 尚未实现同步。", 409)
```

凭据读取：

```python
def _read_credentials(configuration: dict[str, Any]) -> tuple[str, str]:
    """从环境变量读取访问密钥。

    凭据绝不进数据库：写进 configuration 会让数据库备份、审计 payload 和只读数据源接口
    同时变成密钥泄露面。缺失时明确失败而不回退匿名访问——回退会让配置错误表现成
    「桶是空的」，而空清单会被差异计算判成全部删除。
    """

    name = str(configuration.get("credential_env") or "").strip()
    if not name:
        raise AppError("SOURCE_CONFIGURATION_INVALID", "对象存储数据源必须配置 credential_env。", 400)
    access_key = os.getenv(f"{name}_ACCESS_KEY")
    secret_key = os.getenv(f"{name}_SECRET_KEY")
    if not access_key or not secret_key:
        raise AppError(
            "SOURCE_CREDENTIALS_MISSING",
            f"缺少环境变量 {name}_ACCESS_KEY 或 {name}_SECRET_KEY。",
            409,
        )
    return access_key, secret_key
```

`run_sync` 里构造连接器时传入 `settings.max_upload_mb * 1024 * 1024`，并把 `getattr(connector, "skipped", [])` 放进返回结果。**跳过清单不写进 `sync_failure_reason`**——同步是成功的，失败原因字段不该被占用；它进返回值与日志。

`scripts/sync_data_source.py` 的 `create` 增加 `--type object_storage` 分支，接受 `--endpoint`、`--bucket`、`--prefix`、`--credential-env` 等参数，并**拒绝把密钥写进配置**（不提供 `--access-key` 之类的参数）。

- [ ] **Step 4: 运行确认通过**

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_sync_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: 补凭据错误路径的测试**

追加：`object_storage` 数据源缺 `credential_env` → `SOURCE_CONFIGURATION_INVALID`；环境变量缺失 → `SOURCE_CREDENTIALS_MISSING`，且数据库无任何变更、不触发软删除。这两条不需要 MinIO。

Run: `TEST_DATABASE_URL=... uv run pytest backend/tests/test_sync_pipeline.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/data_source_sync.py scripts/sync_data_source.py backend/tests/test_sync_pipeline.py
git commit -m "feat: 同步框架接入对象存储数据源"
```

---

## Task 4: S3 端到端同步与 CI 的 MinIO

**Files:**
- Create: `backend/tests/test_s3_sync.py`
- Modify: `.github/workflows/pytest.yml`

**Interfaces:**
- Consumes: Task 1/2/3 全部
- Produces: CI 的 minio service

- [ ] **Step 1: 写端到端测试**

覆盖与本地目录同构的七条：首次全量；无变化空跑零索引任务；新增；内容更新（重新上传不同内容，ETag 变）；删除软删；熔断；超限对象全程不参与差异。夹具用 `minio` SDK 直接操作桶，每个测试用独立 bucket 名避免互相干扰。

- [ ] **Step 2: 运行确认失败**

Run: `MINIO_ENDPOINT=127.0.0.1:9000 TEST_DATABASE_URL=... uv run pytest backend/tests/test_s3_sync.py -v`
Expected: FAIL（文件尚不存在或断言未满足）

- [ ] **Step 3: 实现并通过**

Run: 同上
Expected: PASS

- [ ] **Step 4: 加 CI 的 minio service**

在 `pytest.yml` 的测试 job 里，与既有 postgres service 并列增加：

```yaml
      minio:
        image: minio/minio:latest
        env:
          MINIO_ROOT_USER: ci-minio
          MINIO_ROOT_PASSWORD: ci-minio-secret
        ports:
          - 9000:9000
        options: >-
          --health-cmd "mc ready local || curl -f http://127.0.0.1:9000/minio/health/live"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
```

> 健康检查命令实现时要实测确认：`minio/minio` 镜像默认 entrypoint 是 `minio` 而非 `server`，
> 需要指定 `command`；`mc` 是否在镜像里也要验证，验不通就退回 `curl` 那条。不要照抄。

并在 job 的 `env` 增加 `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`。

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_s3_sync.py .github/workflows/pytest.yml
git commit -m "feat: 增加对象存储端到端同步与 CI MinIO"
```

---

## Task 5: 文档与验收演练

**Files:**
- Modify: `README.md`、`docs/operations/postgres-migration-recovery.md`

- [ ] **Step 1: 更新 README**

把「本地目录增量同步」小节扩为「数据源增量同步」，补 `object_storage` 的配置样例与 CLI 用法。必须写明三条：**凭据只从环境变量读、绝不进数据库**；**换分段大小重传会触发该对象重新索引**（连同原因）；**超过 `max_upload_mb` 的对象被跳过、不索引、不出现在文档列表里**。

- [ ] **Step 2: 更新运行手册**

在同步的错误码处置表增加 `SOURCE_CREDENTIALS_MISSING`（检查环境变量命名是否与 `credential_env` 一致）、`SOURCE_CREDENTIALS_INVALID`（凭据给错了，与前者区分）、`SOURCE_UNAVAILABLE`（网络/限流，可重试）。

- [ ] **Step 3: 全量质量门**

```bash
uv run ruff check backend evaluations scripts
TEST_DATABASE_URL=... MINIO_ENDPOINT=... uv run pytest -q
uv run python -m scripts.validate_kubernetes
source ~/.nvm/nvm.sh && nvm exec 20.20.2 npm --prefix frontend test -- --run
nvm exec 20.20.2 npm --prefix frontend run lint && nvm exec 20.20.2 npm --prefix frontend run build
```

前端未改动仍需运行。**Node 必须 ≥20.19.0**：低于该版本 npm 静默跳过 rolldown 的平台 binding，测试与构建报 `Cannot find native binding` 且退出码仍是 0。

- [ ] **Step 4: 端到端演练**

用真实 CLI 跑一遍 spec 第 12 节的验收：MinIO 桶放 5 个对象 → 首次全部索引 → 零任务空跑 → 改一个 → 只重建一个 → 删两个 → 软删 → 恢复一个；外加分段上传 ETag 变化、超限跳过、凭据缺失/错误、桶不存在四条。把实际输出附在 PR 描述里。

- [ ] **Step 5: 容器质量门**

本机没有 `docker compose` 插件（只有独立的 `docker-compose` 二进制），且这一项按既往安排在另一台机器执行：

```bash
export POSTGRES_PASSWORD=...
docker compose build
docker compose --profile tools run --rm migrate
docker compose up --detach --wait --wait-timeout 600 postgres backend worker frontend
python scripts/smoke_demo.py http://127.0.0.1:5173 --allow-retrieval-only
docker compose down --volumes --remove-orphans
```

- [ ] **Step 6: Commit**

```bash
git add README.md docs/operations/postgres-migration-recovery.md
git commit -m "docs: 记录对象存储数据源的配置与边界"
```

---

## 计划自查结论

**Spec 覆盖**：spec 第 2 节（ETag 契约）→ Task 1 Step 3 + Task 2 Step 1 的分段测试；第 3 节（超限跳过）→ Task 1 + Task 3；第 4 节（S3 连接器）→ Task 2；第 5 节（凭据）→ Task 3；第 6 节（错误映射）→ Task 2 Step 3/5；第 7 节（协议不改）→ 全程不动 `Connector`；第 8 节（依赖）→ Task 2 Step 3；第 10 节测试表 → 各任务测试步骤；第 12 节验收 → Task 5。第 9 节非目标不产生任务。

**执行时必须实测确认、不得照抄本计划的地方**：

- **CI 里 minio service 的 `command` 与健康检查**（Task 4 Step 4 已标注）。`minio/minio` 镜像需要显式 `server /data`，`mc ready` 是否可用要验证。这是本计划里唯一没有实测依据的部分。
- `S3Error` 除四个已知 code 外的真实取值（网络超时、限流走的是哪个分支）。
- `scripts/sync_data_source.py` 现有 `create` 的参数结构（Task 3 Step 3）。

**已实测确认、可直接使用的事实**：minio SDK 7.2.20；`etag` 不带引号；`list_objects(bucket, prefix=, recursive=)` 返回生成器且分页内部消化；`Object` 有 `object_name`/`etag`/`size`/`last_modified`/`is_dir`；`get_object` 返回 HTTP 响应需 `close()` + `release_conn()`；错误码 `NoSuchBucket`/`NoSuchKey`/`InvalidAccessKeyId`；分段上传 ETag 带 `-N` 后缀且随 `part_size` 变化。

**顺序依赖**：Task 1 → 2 → 3 → 4 → 5 严格串行，前三个都改 `connectors.py` 或依赖其产物。Task 4 的 CI 改动可与 Task 5 的文档并行，但都在最后。
