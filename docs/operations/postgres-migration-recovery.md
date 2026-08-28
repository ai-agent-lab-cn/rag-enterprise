# PostgreSQL 迁移与恢复运行手册

本流程必须由操作者显式执行。应用启动只校验 schema 版本，不创建表、不导入旧数据。

## 迁移前

1. 停止写入，并保留现有 `data/` 目录的只读快照。
2. 为目标 PostgreSQL 创建空数据库并安装 `vector` 扩展权限。
3. 升级已有 schema 2 数据库时，先确认同一文档版本没有多个活动任务；查询必须返回零行：
   `SELECT document_version_id FROM index_jobs WHERE status IN ('queued', 'running') GROUP BY document_version_id HAVING count(*) > 1;`
4. 执行 `uv run python scripts/database_migrate.py apply`。
5. 执行 `uv run python scripts/database_migrate.py check --required-version 4`。
6. 使用原有备份工具备份旧 `data/`，验证备份并完成一次空目录隔离恢复。

## 全量导入与校验

执行：

```bash
uv run python scripts/legacy_to_postgres.py --data-root data
```

导入在单一数据库事务中完成。目标业务表非空时停止；同一源数据重复执行会返回首次导入计数。账号密码哈希和知识库授权会保留，旧 sessions 不导入，因此旧令牌全部失效。

核对命令输出中的账号、知识库、授权、文档、版本和 chunk 数量。原始文件存在时，同时核对文件 SHA-256 和大小；缺失原始文件的历史文档会记录 `missing/` 路径和零字节，必须在切换前人工处置。

## PostgreSQL 与原始文件备份

```bash
uv run python scripts/postgres_backup.py backup \
  --uploads-root data/uploads \
  --output /secure/off-host/rag-postgres.tar.gz
uv run python scripts/postgres_backup.py verify \
  --backup /secure/off-host/rag-postgres.tar.gz
```

备份包包含 `pg_dump` 自定义格式数据库备份、原始文件和逐文件 SHA-256 清单，不包含连接串或密钥。

## 隔离恢复与回滚

恢复目标必须是新建空数据库和空上传目录：

```bash
uv run python scripts/postgres_backup.py restore \
  --backup /secure/off-host/rag-postgres.tar.gz \
  --uploads-target /tmp/rag-restored/uploads
uv run python scripts/database_migrate.py check --required-version 4
```

切换前若校验失败，保持旧版本和旧数据目录不变，删除失败的隔离目标后重新处理；不得在原目标上局部补写。运行时正式切换属于下一阶段，不在本阶段执行。

## PostgreSQL 运行模式

首次启动或升级时，先显式执行迁移，再启动 API、Worker 和前端：

```bash
export POSTGRES_PASSWORD=...   # 必填，compose 缺少它会拒绝启动
docker compose --profile tools run --rm migrate
docker compose up --detach --wait postgres backend worker frontend
```

Chroma 移除后 `docker-compose.yml` 自身即是完整拓扑（postgres、migrate、backend、
worker、frontend），不再需要叠加 `docker-compose.postgres.yml` 这层 override。

API 上传只创建不可变原始文件、文档版本和幂等任务。Worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务；成功后在同一事务中写入 chunks 并切换当前版本，失败重试达到上限后保留上一可用版本。

## 索引重建

切分参数（`CHUNK_SIZE`、`CHUNK_OVERLAP`）或切分算法版本变更后，存量文档仍然使用旧配置的
分块。重建由操作者显式发起，不随应用启动或上传自动触发：

```bash
DATABASE_URL='<目标库>' uv run python -m scripts.rebuild_index start \
  --knowledge-base kb_default --chunk-size 400 --chunk-overlap 60
DATABASE_URL='<目标库>' uv run python -m scripts.rebuild_index status --batch <批次 ID>
DATABASE_URL='<目标库>' uv run python -m scripts.rebuild_index inventory --knowledge-base kb_default
```

重建任务与普通索引任务共用 `index_jobs` 队列，因此共享重试上限、退避与租约超时恢复，
由同一批 Worker 消费。语义边界：

- 只覆盖 `current_version_id` 指向的版本；历史版本不参与检索，不重切。
- 不创建新的文档版本，也不移动当前版本指针，`document_versions.status` 保持 `ready`。
- 重建写入一个新的 `building` 索引版本，**旧分块完整保留**。用户检索按 active 索引版本
  过滤，因此重建期间完全看不到新分块，也不会看到两套切分配置混合的结果。
- 续跑判定按"目标索引版本是否已覆盖该文档"，重复执行 `start` 安全；已排队或运行中的
  版本会被跳过。同一知识库只允许一个 `building` 版本，换目标配置发起会被
  `REBUILD_IN_PROGRESS` 拒绝。
- 重建失败只把任务标记为 `failed`，上一批分块与文档状态保持不变，文档继续可检索；
  索引版本转为 `failed`，active 指针不动。
- `inventory` 输出各索引版本的切分配置与覆盖文档数，并存期间会同时列出新旧两套。

## 索引版本切换与回滚

重建完成后需要显式切换才会生效，命令与四道校验见
[README 的索引版本切换与回滚](../../README.md#索引版本切换与回滚)。运行要点：

- `switch_index status --batch <id>` 会顺带把跑完的批次推进到 `ready` 或 `failed`。
  停在 `building` 说明还有任务未完成；转 `failed` 说明有任务终态失败或覆盖不全，
  此时不要切换，先用 `rebuild_index start` 续跑或排查失败原因。
- 切换被拒时按错误码处置：`INDEX_CONFIG_MISMATCH` 表示报告不是用该版本的配置跑的，
  重新用相同 `--chunk-size` / `--chunk-overlap` 生成报告；`INDEX_REPORT_INCOMPLETE`
  表示报告缺配置指纹（1.0.0 的历史报告都缺，不能用于放行）；`INDEX_QUALITY_REGRESSED`
  表示指标未达阈值或相对基线回退，属于该重新审视配置而不是绕过门禁的情形。
- **回滚只有一次机会**：回滚把原 active 降为 `ready` 而非 `previous`，之后不再存在可回滚
  目标。要再切回去需重新提供合格报告执行 `switch`。
- 确认不再需要旧版本后再执行 `retire` 释放磁盘。`retired` 只表示"可以删除"，
  数据仍在库中直到显式清理；`active` 与 `previous` 版本会被 `INDEX_VERSION_IN_USE` 拒绝清理。

更换 embedding 模型不在本流程范围。`chunks.embedding` 自 Schema V10 起按登记维度固定
（pgvector 拒绝为无维度列建 ANN 索引），因此新旧模型维度不同时无法并存回滚，
必须另行规划停写窗口与全量重嵌入。
