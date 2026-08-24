# 生产化阶段 5 发布验收记录

## 状态

本记录对应 [#86](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/86)。最终工程结论只引用该 Issue、
实施 PR、main commit 与 CI；运行数据由 PR/CI 附件保存，不在本文复制维护。

## 必需证据

| 验收项 | 证据位置 |
| --- | --- |
| SLI 原始样本、内部阈值与判定 | PR/CI 的 `controlled-pilot` 附件 |
| 迁移数量与引用完整性 | PR/CI 的 `production-inventory` 附件 |
| 备份与隔离恢复 | #86 中链接的演练记录 |
| API、Worker、PostgreSQL 故障恢复 | #86 中链接的演练记录 |
| 管理员、成员、旧会话与浏览器流程 | PR Playwright CI |
| 最终代码与质量门 | 实施 PR、main commit、main CI |

## 风险与云生产准入条件

- Docker Desktop Kubernetes 是单节点本地环境，不具备跨节点容灾能力。
- 内部阈值只用于受控试运行，不构成对外 SLO；正式 SLO 需要公网环境的长期样本与责任确认。
- 正式云环境必须补齐多副本、共享限流、托管 PostgreSQL/对象存储、集中告警、TLS/WAF、密钥轮换、
  异地备份、容量预算和恢复责任人。
- 当前不接入不可信公开上传流量，企业 SSO、恶意文件扫描和外部连接器不在 #86 范围。
- PostgreSQL 模式的不可变版本回滚仍由阻塞缺陷
  [#87](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/87) 跟踪；完成前不得作最终发布放行。
- Render 仍由延期的 [#46](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/46) 管理，未经再次批准不得部署。

## 放行结论

在 #86 的 PR 与 main CI 全部通过前保持“未放行”。任何失败证据必须保留并修复后重新执行完整门禁。
