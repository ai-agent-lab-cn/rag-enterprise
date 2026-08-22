# PostgreSQL 迁移与恢复运行手册

本流程必须由操作者显式执行。应用启动只校验 schema 版本，不创建表、不导入旧数据。

## 迁移前

1. 停止写入，并保留现有 `data/` 目录的只读快照。
2. 为目标 PostgreSQL 创建空数据库并安装 `vector` 扩展权限。
3. 执行 `uv run python scripts/database_migrate.py apply`。
4. 执行 `uv run python scripts/database_migrate.py check --required-version 1`。
5. 使用原有备份工具备份旧 `data/`，验证备份并完成一次空目录隔离恢复。

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
uv run python scripts/database_migrate.py check --required-version 1
```

切换前若校验失败，保持旧版本和旧数据目录不变，删除失败的隔离目标后重新处理；不得在原目标上局部补写。运行时正式切换属于下一阶段，不在本阶段执行。
