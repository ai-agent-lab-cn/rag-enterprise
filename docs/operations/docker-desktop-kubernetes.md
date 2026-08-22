# Docker Desktop Kubernetes 生产演练手册

本手册对应 [Issue #84](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/84)。它只用于单机
Docker Desktop Kubernetes 的受控试运行演练，不代表多节点容灾，也不对外承诺正式 SLO、RPO
或 RTO。Render、正式域名和线上流量不在本阶段范围。

## 组件与边界

- CloudNativePG 1.30.0 管理单实例 PostgreSQL 16；数据库镜像固定摘要并包含 pgvector。
- API、索引 Worker 和前端分别运行；数据库迁移是显式 Job，应用启动只校验 schema 版本。
- `rag-uploads` 保存当前原始文件，`rag-backups` 保存显式生成的备份，两个 PVC 不混用。
- NodePort `30080` 仅供本机访问；配置使用 `APP_ENVIRONMENT=test`，明确它不是正式生产入口。
- Secret 只保存在被 Git 忽略的 `deploy/kubernetes/secret.yaml`，不得提交真实密码或 API Key。

## 前置条件

1. 在 Docker Desktop 中启用 Kubernetes，确认 context 为 `docker-desktop`。
2. 将 `/Applications/Docker.app/Contents/Resources/bin` 加入 `PATH`，确认 `kubectl` 可用。
3. 为 Docker Desktop 分配至少 8 GiB 内存和 4 CPU；中文 embedding 模型首次加载需要时间。
4. 复制 `secret.example.yaml` 为 `secret.yaml` 并替换密码，或使用本地密码管理流程预先创建
   `rag-enterprise-secrets`。数据库与文件备份不包含 Secret；恢复凭据必须单独加密保管。

脚本会拒绝在非 `docker-desktop` context 上执行写操作，防止误操作其他集群。

## 部署

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
./scripts/kubernetes_rehearsal.sh validate
./scripts/kubernetes_rehearsal.sh install-operator
./scripts/kubernetes_rehearsal.sh build
./scripts/kubernetes_rehearsal.sh deploy
./scripts/kubernetes_rehearsal.sh status
```

`build` 会把两个本地镜像显式导入 `docker-desktop` 的 kind 节点，工作负载使用
`imagePullPolicy: Never`；这避免同名旧缓存或公共镜像意外替代当前工作区代码。

访问 `http://127.0.0.1:30080`。首次环境需要在登录页显式初始化管理员；不要在清单中预置账号。

## 观测基线

受控试运行只采集并核对指标，不设正式 SLO：

| 信号 | 证据与检查方法 |
|---|---|
| API 可用性 | `/api/health/live` 与 `/api/health/ready`，以及 Deployment 可用副本 |
| 请求延迟/错误 | 管理员 `/api/system/metrics` 的 requests/routes 汇总，不含业务正文 |
| 检索失败 | `/api/system/metrics` 的 rag queries/failures 与 total latency |
| 索引积压/失败 | `index_jobs` 按 queued/running/failed 分组及最早 `available_at` |
| 数据库容量 | PostgreSQL `pg_database_size('rag_enterprise')` |
| 文件存储容量 | API/Worker 中 `df -h /app/data/uploads`，以及 PVC requested capacity |

建议每次演练记录 commit、检查时间、请求总数与错误数、P95（外部压测计算）、索引积压、失败任务、
数据库字节数、上传卷使用率和操作者。进程内指标会随 API Pod 重启归零，因此不能充当长期监控存储。

可使用只读采集脚本汇总这些证据；若设置临时 `ADMIN_TOKEN`，脚本还会读取管理员进程指标：

```bash
./scripts/kubernetes_observe.sh
ADMIN_TOKEN='<当前会话令牌>' ./scripts/kubernetes_observe.sh
```

## 备份与隔离恢复

显式创建一次备份 Job：

```bash
./scripts/kubernetes_rehearsal.sh backup
kubectl get jobs -n rag-enterprise
kubectl logs -n rag-enterprise job/<实际任务名>
./scripts/kubernetes_rehearsal.sh restore-drill
```

备份写入 `rag-backups` PVC，并包含数据库自定义格式 dump、原始文件和 SHA-256 清单。恢复必须创建
新的空数据库与空上传目标，使用 `scripts/postgres_backup.py restore` 完成；`restore-drill` 会使用临时
`rag_restore_drill` 数据库和 `emptyDir`，由数据库管理员先创建 `vector` 扩展，再以应用账号恢复并核对核心数据计数；完成后删除临时数据库与临时 Secret。禁止覆盖
正在服务的数据库或 `rag-uploads`。每次恢复记录实测 RPO/RTO，不把单次结果写成承诺。

## 重启与恢复演练

先确保没有正在上传的文件，再显式确认本地破坏性演练：

```bash
./scripts/kubernetes_rehearsal.sh drill --confirm-local-restart
```

脚本依次重启 API、Worker 和单实例数据库，等待恢复后核对文档数、索引任务数和任务状态。数据库重启
期间服务短暂不可用是单实例环境的预期限制；这正是不能将 Docker Desktop 视为最终云生产环境的原因。

## 失败处理与清理

失败时保留 Pod 日志、事件、任务状态和当前 commit，不记录正文或密钥。先检查 `kubectl describe`、
Pod 日志、PVC 与数据库 Cluster 状态；不要隐式执行迁移或删除数据卷。

删除普通工作负载不会删除 PVC。彻底删除命名空间会删除本地数据库和文件，必须先验证备份，并由操作者
单独明确执行；本仓库不提供自动删除命令。
