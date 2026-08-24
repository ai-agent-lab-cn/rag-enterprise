# 单组织受控试运行与发布验收

本手册承载 [#86](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/86) 的执行口径。Docker Desktop
Kubernetes 只用于本地生产演练；本流程不接入公网流量、不部署 Render，也不形成对外 SLO。

## SLI 与内部阈值

策略唯一配置为 `config/controlled-pilot.json`。报告必须保留 `external_slo=false`，并使用以下规则：

- 可用性：就绪探测成功样本数除以全部样本数。
- 延迟：就绪探测耗时的最近秩 P95。
- 检索失败率：窗口内 RAG 失败计数增量除以查询计数增量。
- 索引失败率：窗口结束时失败任务除以已结束任务。
- 索引积压：窗口内最老等待任务的最大等待秒数。
- 容量：数据库、原始文件和备份占各自 PVC 申请容量的比例。
- 缺失样本、计数器重置、零业务分母或窗口不足均失败关闭，不以零值或旧窗口补齐。

默认窗口为 5 分钟、至少 12 个样本。默认阈值只用于内部试运行门禁，不可显示为客户承诺。

## 执行试运行

先按 [Docker Desktop Kubernetes 手册](docker-desktop-kubernetes.md)确认集群健康，再使用临时管理员
会话执行：

```bash
ADMIN_TOKEN='<临时会话令牌>' ./scripts/kubernetes_controlled_pilot.sh
```

脚本会执行只读就绪探测和默认知识库问答、采集数据库与 PVC 容量，并在
`artifacts/controlled-pilot/` 生成 JSON Lines 原始样本和 JSON 判定报告。令牌不会写入报告。
运行结束后应撤销该会话。原始样本和报告作为 CI/PR 附件保存，不提交到仓库。

## 迁移、身份与权限核对

1. 按 [PostgreSQL 迁移手册](postgres-migration-recovery.md)冻结写入、备份并执行全量迁移。
2. 保存 `legacy_to_postgres.py` 的计数输出，不保存连接串。
3. 通过环境变量连接隔离目标并执行：

```bash
DATABASE_URL='<隔离目标>' uv run python scripts/production_inventory.py \
  --expected artifacts/migration-counts.json \
  --output artifacts/production-inventory.json
```

4. 核对账号、角色、知识库授权、文档、版本、chunk/向量；目标 sessions 必须为零。
5. 验证旧令牌被拒绝，并由管理员和普通成员分别重新登录，检查管理接口拒绝与知识库授权边界。

## 故障与恢复桌面演练

- 使用 `kubernetes_rehearsal.sh drill --confirm-local-restart` 逐项重启 API、Worker 和数据库。
- 新建显式备份后运行 `restore-drill`，只恢复到临时数据库与临时文件目标。
- 按 [发布回滚手册](release-rollback.md)验证前一不可变镜像；禁止使用 `latest`。
- 记录事件开始、检测、处置、恢复和数据核对时间，不隐藏人工步骤。

## 发布准入

只有以下证据全部通过，#86 才可关闭：SLI 报告、迁移清单、备份与隔离恢复、故障演练、浏览器
端到端、PR CI 和 main CI。最终报告必须列明单节点、无公网 WAF/企业 SSO、无跨节点存储容灾、
内部阈值尚非正式 SLO等限制。正式云环境还需重新评审多副本、共享限流、托管数据库/对象存储、
TLS/WAF、密钥管理、集中监控告警、备份保留和成本预算。
