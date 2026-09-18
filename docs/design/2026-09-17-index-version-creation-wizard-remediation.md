# 创建索引版本向导修复设计

日期：2026-09-17

状态：本地实现完成，尚未执行自动化验证

## 目标

在保留 Preview、三指纹校验、幂等提交和原子创建能力的前提下，修复“创建索引版本”五步向导中配置定义不持久、配置快照语义不一致、排除资料缺少确认、异步预览竞态、最终确认信息不足等问题。

## 五步业务语义

1. **创建场景**：展示系统检测到的配置变化、文档变化和阻塞原因；用户选择业务目的。
2. **目标配置**：Chunking 是当前可编辑配置；Parser、Embedding、索引结构和 Reranker 是只读运行事实。
3. **文档快照**：展示纳入、排除、增删改及排除明细；存在排除资料时必须明确确认。
4. **变更与影响**：展示配置差异、文档影响、粗略规模和预览时构建容量。
5. **确认并构建**：汇总原因、配置、范围、排除确认和三个指纹；成功后定位新版本与 Build。

## 配置定义

- 新建知识库级 `index_definitions`，只持久化当前真实可编辑的 `chunk_size`、`chunk_overlap`。
- Parser、Embedding、Vector、Keyword、Metadata、ACL、Citation 和 Reranker 继续从真实运行源聚合，不复制成可编辑配置。
- 没有 Definition 行时回退应用默认值；首次成功创建候选版本时原子写入 Definition。
- Preview 不写数据库；Create 事务同时冻结 Definition、Config Snapshot、Document Snapshot、Version、Build、Operation 和 Jobs。
- Active 与 Definition 不同表示真实配置 drift；用户修改 Chunking 创建并激活后不应因回退到应用默认值产生伪 drift。

## 配置快照

- `parser.schema_version` 固定表示全局解析结构协议版本。
- `parser.runtime_versions` 保存当前文档集合涉及的实际 Parser 版本集合。
- Preview 与最终持久化 Version 使用同一结构。

## 文档范围策略

- 源文件丢失继续是硬阻塞，并提供结构化明细。
- 相同内容重新上传时，如果登记路径下的源文件已缺失，安全恢复物理文件，不创建重复 Document Version 或 Index Job。
- 解析失败或没有 current revision 的资料允许排除，但创建前必须显式确认；确认事实随创建请求写入 Version。

## 状态与异常

- Preview 使用独立 Loading 和请求序号，旧响应不得覆盖新输入。
- Preview 后配置、文档或组件变化时，停留在向导并提供“重新生成预览”。
- 创建成功与页面刷新失败分开处理；成功结果不能被误报为创建失败。
- 创建成功后保留 Version ID、Build ID，并在版本治理页面定位新记录。

## Active 与数据同步边界

当前普通索引任务会向 Active Version 写入新 Chunk，这是已有架构行为。彻底拆分需要把解析产物从 Chunk/Embedding 中独立出来，并重构 Data Sync → Document Asset → Index Governance 的任务边界。本轮不做不完整的局部禁写，以免新上传资料永久停在不可检索状态；本轮删除“快照绝对不可变”的误导文案，并将该跨层重构保留为独立后续阶段。

## 本轮实现结果

- 新增数据库迁移 V40：知识库级 Chunking Definition，以及“已确认排除资料”版本事实。
- 创建场景按普通配置变化、组件升级、文档集合变化和相同输入主动重建分别判定；一致性修复在没有健康检查证据前不开放。
- Preview 与 Create 使用一致的 Parser Snapshot，并在提交时重新校验配置、文档集合、组件版本、源文件和构建容量。
- 五步页面改为“创建场景 → 目标配置 → 文档快照 → 变更与影响 → 确认并构建”，补齐中文字段、纳入/排除明细、排除确认、三指纹和失败后重新预览。
- 相同内容重新上传可恢复缺失的物理源文件，同时保持 Document Version 与 Index Job 幂等。
- 创建成功后显示 Version/Build ID 并打开新版本详情；列表刷新失败使用独立提示，不再误报为创建失败。

## 尚未验证与后续项

- 按本轮约定，未运行测试、Lint、类型检查、生产构建或真实页面验收。
- V40 迁移尚未在当前本机数据库实际执行。
- Active Version 与普通数据同步写入的彻底解耦未实施，仍按上节作为独立架构改造处理。

## 非目标

- 不开放多 Parser、多 Embedding 或索引结构编辑器。
- 不自动激活候选版本。
- 不 Commit、不 Push。
- 不清理现有测试数据。
