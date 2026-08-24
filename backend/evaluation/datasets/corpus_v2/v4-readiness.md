# V4 生产就绪验收报告

## 结论与边界

V4 候选版本已具备创建最终版本制品的工程条件。最终放行以
[#64](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/64) 的关闭结论、
[`v4.0.1` Release](https://github.com/ai-agent-lab-cn/rag-enterprise/releases/tag/v4.0.1) 及其附件为准。
本结论只覆盖当前单实例架构的可发布、可诊断和可恢复能力，不代表公网环境可用性或线上 SLO。
Render 部署仍由 [#46](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/46) 承载，须再次明确批准。

## 验收证据索引

| 能力 | Issue / PR | main commit / CI |
| --- | --- | --- |
| 身份、会话、角色与知识库授权 | [#58](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/58) / [PR #65](https://github.com/ai-agent-lab-cn/rag-enterprise/pull/65) | [`86da5e2`](https://github.com/ai-agent-lab-cn/rag-enterprise/commit/86da5e286e8b32cc05d7af439017e1cb9d447346) / [CI](https://github.com/ai-agent-lab-cn/rag-enterprise/actions/runs/31984195990) |
| 安全、隐私与数据最小化 | [#59](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/59) / [PR #66](https://github.com/ai-agent-lab-cn/rag-enterprise/pull/66) | [`da09d5d`](https://github.com/ai-agent-lab-cn/rag-enterprise/commit/da09d5dd57b28b84174dbe7ab2d7877559539c3a) / [CI](https://github.com/ai-agent-lab-cn/rag-enterprise/actions/runs/31987199400) |
| 日志、指标、审计与诊断 | [#60](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/60) / [PR #67](https://github.com/ai-agent-lab-cn/rag-enterprise/pull/67) | [`ed84c0f`](https://github.com/ai-agent-lab-cn/rag-enterprise/commit/ed84c0fcb370cf779d5102726b6cab7801fe4e67) / [CI](https://github.com/ai-agent-lab-cn/rag-enterprise/actions/runs/31994746702) |
| 备份、恢复与生命周期 | [#61](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/61) / [PR #68](https://github.com/ai-agent-lab-cn/rag-enterprise/pull/68) | [`2cd479f`](https://github.com/ai-agent-lab-cn/rag-enterprise/commit/2cd479f684cbd6c4d08d0de2ef3f43249b0ab4a3) / [CI](https://github.com/ai-agent-lab-cn/rag-enterprise/actions/runs/32015190209) |
| 管理控制面 | [#62](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/62) / [PR #69](https://github.com/ai-agent-lab-cn/rag-enterprise/pull/69) | [`163147b`](https://github.com/ai-agent-lab-cn/rag-enterprise/commit/163147b06759faf5649f2292fd1257e46daeeeff) / [CI](https://github.com/ai-agent-lab-cn/rag-enterprise/actions/runs/32019133159) |
| 版本制品与受控回滚 | [#63](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/63) / [PR #70](https://github.com/ai-agent-lab-cn/rag-enterprise/pull/70) | [`4dc6a43`](https://github.com/ai-agent-lab-cn/rag-enterprise/commit/4dc6a43659749a780704d0ce9c4965dc4b85d2b2) / [CI](https://github.com/ai-agent-lab-cn/rag-enterprise/actions/runs/32022918823) |

发布时的 Tag、完整 commit、后端与前端镜像 digest、前一版本回滚镜像 digest 和隔离恢复结果，
由 Release 的 `artifact-manifest.json` 与 `rollback-evidence.json` 唯一记录。发布工作流失败时不会创建
Release，并保留失败诊断附件。

## 验收口径

- 全栈质量门覆盖后端测试与静态检查、前端测试与构建、容器健康和基础问答。
- 权限场景覆盖匿名拒绝、管理员能力、成员知识库授权隔离、会话撤销和管理接口拒绝。
- 安全与隐私检查以 [威胁清单](../security/v4-threat-model.md) 和对应自动化测试为准。
- 审计事件不记录密码或业务正文，审计链篡改检测、指标权限和健康诊断均进入质量门。
- 备份恢复以 [运行手册](../operations/backup-recovery.md) 为准；发布前还须通过当前版本写入、
  隔离恢复、前一版本健康与数据读取验证。
- 发布必须从已通过 main CI 的 `v4.x.y` Tag 触发，部署和回滚只能使用 Release 清单中的 digest。

## 已知风险与接受边界

| 风险 | 当前边界 | 复查点 |
| --- | --- | --- |
| 进程内限流不跨实例共享 | 当前只验收单实例；多实例需共享限流或网关 | 启动 #46 前 |
| 无外部 WAF、恶意软件扫描和企业 SSO | 不接收不可信主动文件，不宣称企业安全控制完备 | 正式托管方案评审 |
| 无正式流量与长期监控样本 | 只报告 CI 与隔离演练结果，不宣称线上 SLO | 公网环境稳定性观察后 |
| 文件型存储与备份需停写保持一致性 | 按运行手册冻结写入并校验备份 | 每次正式恢复演练 |
| 降级只验证 V3 数据读取兼容性 | 失败即停，不自动迁移或覆盖现有数据 | 每个后续版本发布 |

上述风险仅在“未部署 Render、无正式流量、单实例”的运行边界内接受；阶段接受人与最终放行记录
以 [#64](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/64) 为准。

## V0→V4 总复盘

- V0 建立仓库事实源、基础 CI 和阶段治理，使后续变更都有 Issue、PR、commit、CI 与 Tag 证据。
- V1 把 MVP 收敛为可测试、可构建、可容器启动的工程基线。
- V2 用固定数据集、真实向量存储和精排评测建立检索质量门。
- V3 补齐多知识库、持久会话、来源一致性、回答评测和稳定降级，冻结回答质量基线。
- V4 在不改变 V2/V3 算法基线的前提下补齐权限、安全、隐私、审计、可观测、恢复与不可变发布。

保留：小步 Issue、独立 PR、main CI 后再进入下一项，以及 Release 直接保存不可变事实。
停止：把托管部署当作代码完成的默认后续动作，或用 `latest`、本地文字记录代替版本证据。
改进：正式托管前重新评审平台额度、持久存储、共享限流、TLS/WAF、告警和真实 SLO。

治理状态与最终结论分别记录在 [总控 #1](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/1)、
[V4 阶段门 #8](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/8) 和
[放行记录 #9](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/9)。
