# Render 免费 Demo 部署

本方案只用于 V3 演示与验收，不是 V4 生产部署。仓库根目录的 `render.yaml` 同时定义：

- `rag-enterprise-cn-web`：React 静态站点；
- `rag-enterprise-cn-api`：FastAPI 免费 Web Service；
- `/api/*`：由静态站点转发到后端，浏览器继续使用现有同源 API 地址；
- `main` CI 全部通过后才自动部署。

## 首次创建

1. 登录 Render，选择 **New > Blueprint**。
2. 连接 `ai-agent-lab-cn/rag-enterprise`，Blueprint 路径保持 `render.yaml`。
3. 在首次创建页面填写 `GEMINI_API_KEY`，不要把密钥写入仓库。
4. 等待后端 `/api/health` 通过，再打开 `https://rag-enterprise-cn-web.onrender.com`。

若服务名已被占用，需要同时修改 `render.yaml` 中的服务名、前端 `/api/*` Rewrite 目标和
`FRONTEND_ORIGIN`，保持三处一致。

## 演示数据与模型边界

后端每次在全新的临时文件系统启动时，会把公开资料
`knowledge/project-profile.md` 导入默认知识库。已有文档时不会重复导入。

免费实例使用资源较小的模型，以适配 512 MB 内存：

- Embedding：`BAAI/bge-small-zh-v1.5`
- Reranker：`demo/lexical-overlap-v1`（不加载第二个 Transformer 的确定性词法精排）

这两个覆盖项只服务在线 Demo，目的是在免费实例的 512 MB 内存中保留“召回后再排序”的
完整链路；它们不替换 V2/V3 正式评测中的冻结模型，也不能作为正式质量分数。
页面技术信息会展示真实运行模型，右上角明确标记“演示环境 · 数据可能重置”。

## 免费环境限制

- 空闲后会休眠，首次访问可能需要约一分钟冷启动；
- 重启、重新部署或休眠后，本地文件系统可能重置；
- 不挂载付费持久化磁盘，不承诺用户上传资料和会话跨实例生命周期保留；
- 只上传不含电话、邮箱、令牌等敏感信息的公开演示资料。

限制以 Render 官方的 [Free 实例说明](https://render.com/docs/free) 为准。

## 部署后验证

正式验收必须通过前端公开地址运行，不绕过静态站点的 `/api/*` Rewrite：

```bash
python scripts/smoke_demo.py https://rag-enterprise-cn-web.onrender.com
```

脚本依次验证：健康状态、默认知识库演示资料、基础问答状态和
`project-profile.md` 来源。只有正式 Demo 配置了 Gemini 并返回 `answered` 才通过；
`--allow-retrieval-only` 只允许在未配置密钥的本地或 CI 容器中使用。

验收结果应回写到 V3 #46 和证据索引 #9，并链接部署 URL、commit、PR 和 main CI；
不要在文档中手工维护“当前是否已部署”的状态。

## 参考

- [Render Blueprint 规范](https://render.com/docs/blueprint-spec)
- [静态站点 Rewrite](https://render.com/docs/redirects-rewrites)
- [健康检查](https://render.com/docs/health-checks)
- [部署触发条件](https://render.com/docs/deploys)
