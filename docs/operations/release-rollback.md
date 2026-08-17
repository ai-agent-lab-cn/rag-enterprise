# V4 版本发布与受控回滚

发布工作流只响应 `v4.x.y` Tag。创建 Tag 前必须确认目标 commit 位于 `main`，且对应的
`quality` 主分支工作流已全部通过。#63 的 PR 只建立发布能力；真正的 V4 Tag、GHCR 镜像和
GitHub Release 必须等待阶段最终批准。

发布时会为后端和前端分别建立版本 Tag 与 `sha-<commit>` 标签，并在 Release 的
`artifact-manifest.json` 中记录内容 digest。部署或回滚必须使用 `image@sha256:...`，不得依赖
`latest` 或可变标签。

首次建立 V4 制品链时，工作流还会从前一版本 Git Tag 构建带有
`rollback-<tag>-sha-<commit>` 唯一标签的回滚镜像，使历史 commit、历史版本标签与不可变
digest 可追溯，同时不覆盖历史版本标签。随后在隔离临时目录中：

1. 启动当前版本镜像并创建最小持久数据；
2. 停止写入，创建并校验备份；
3. 恢复到新的空目录，不覆盖当前数据；
4. 使用前一版本镜像 digest 启动恢复副本；
5. 验证健康和已有数据读取；
6. 生成 `rollback-evidence.json`，通过后才创建 GitHub Release。

任何构建、完整性、兼容性或回滚检查失败都会停止 Release，并上传可用的诊断文件。工作流仅
授予 `contents: write`、`packages: write` 和 `actions: read`；使用 GitHub 临时令牌，不接收
Render、Gemini 或正式环境密钥。此流程不创建 Render 服务，不切换公网流量，也不证明线上 SLO。
