# PostgreSQL 版本发布与受控回滚

发布工作流只响应 `v5.x.y` Tag。创建 Tag 前必须确认目标 commit 位于 `main`，对应的 `quality`
主分支工作流全部通过，并且已经明确批准创建 Release。本流程不会部署 Render 或切换公网流量。

## 不可变制品

后端、Worker 和前端必须使用 `registry@sha256:digest`。PostgreSQL/pgvector 镜像同样固定 digest，
不得使用 `latest` 或可变版本标签。首个 v5 Release 尚无前序 v5 Release 时，仓库管理员必须配置
`POSTGRES_ROLLBACK_BASE_COMMIT`：它必须是位于 main、已通过 main quality 且支持当前 PostgreSQL
schema 的 40 位 commit。后续版本只使用最近的正式 v5 Release 作为前一版本。

## 隔离回滚流程

`scripts/verify_release_rollback.sh` 使用两个不同的 Compose project 和两套不同的 bind mount：

1. 在当前 PostgreSQL 目录显式执行迁移，再启动当前 API、Worker 和前端。
2. 创建管理员、成员及知识库授权，并写入文档、版本、向量和已完成索引任务的最小证据。
3. 使用 `postgres_backup.py` 备份数据库和原始文件并验证 SHA-256 清单。
4. 停止当前写入，将备份恢复到新的空 PostgreSQL 目录和空原始文件目录。
5. 不执行降级迁移，直接以“前一版本”不可变后端、Worker 和前端启动隔离恢复副本。
6. 验证 schema 兼容、健康、管理员/成员重新登录、知识库权限，以及账号、文档版本、向量、任务等
   持久业务数量与当前版本一致。登录会创建新会话，因此会话数量不作为前后完全相等的断言。
7. 生成不含密码、令牌或连接串的 `rollback-evidence.json`。

任何镜像不是 digest、备份不完整、schema 不兼容、前一版本启动失败、权限或数据数量不一致，都会
停止 Release。失败时保留当前正式数据库与原始文件，不在其上执行补写、恢复或破坏性降级迁移。

## 本地合同演练

本地只允许显式使用 Docker 内容寻址 image ID，并且当前/前一镜像可暂时使用相同构建来验证流程：

```bash
ALLOW_LOCAL_IMAGE_IDS=true \
CURRENT_BACKEND_IMAGE='sha256:…' CURRENT_FRONTEND_IMAGE='sha256:…' \
PREVIOUS_BACKEND_IMAGE='sha256:…' PREVIOUS_FRONTEND_IMAGE='sha256:…' \
RELEASE_POSTGRES_IMAGE='sha256:…' \
ROLLBACK_EVIDENCE_PATH='artifacts/postgres-rollback.json' \
./scripts/verify_release_rollback.sh
```

相同镜像的本地演练只证明编排、备份、隔离恢复和证据生成合同可执行；正式 Release 仍必须使用不同
版本的 registry digest 验证实际向后兼容性。
