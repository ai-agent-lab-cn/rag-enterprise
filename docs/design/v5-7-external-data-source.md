# V5-7 设计：外部数据源接入（S3 兼容对象存储）

> 状态说明：本文保留 S3 连接器的底层设计。页面与 Sync Run 治理由
> `docs/design/v5-7-multi-source-management.md` 补充，原“前端不实现”边界已失效。

日期：2026-08-28
基线 commit：`6ac809a`（Schema V11，数据同步 Pipeline 已完成，向量存储只有 pgvector）
上游归属：[#92 V5 总控](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/92) 第 4 项
「企业数据源：先定义连接器协议，再实现一个可真实验收的外部来源及增量同步」的**后半部分**。
前半（连接器协议 + 同步框架 + 本地目录实现）已由
[#100 V5 阶段 6](https://github.com/ai-agent-lab-cn/rag-enterprise/issues/100) 完成。

**本阶段完成后 #92 第 4 项才算达成。** V5-6 的本地目录不是「外部来源」。

## 1. 目的

接入 S3 兼容对象存储，让知识库能从企业实际存放文档的地方增量同步。同时——这是本阶段
第二个同等重要的目的——**用第二个真实实现检验 V5-6 定下的连接器协议**。

V5-6 的 spec 第 3 节写过：只有一个实现时任何抽象都可能藏着该实现特有的假设，S3 会成为
第二个真实实现，本地目录的 `version` 是**自己算的**内容哈希、S3 的是**服务端给的** ETag，
这个差异会暴露协议里任何「假定 version 可以本地计算」的假设。若发现协议需要调整，
那是预期结果而非返工。

**检验结果：协议本身不用改，但它的 `version` 契约必须收紧。** 见第 2 节。

## 2. ETag 与 version 契约（本阶段最核心的取舍）

V5-6 给 `version` 定的契约是「**内容变了才变，内容没变就不变**」。S3 的 ETag 满足前半，
不满足后半。这是实测结论，不是理论担忧——用 MinIO 把同一份 40MB 内容分别以 5MB 和 16MB
分段上传：

```
small-parts.md   52e714456418be92f59e9eea2189ece9-9    ← 5MB 分段，9 段
large-parts.md   40820467919c684a8c89388304bcd584-3    ← 16MB 分段，3 段
single.md        890fdd7ea3c488fa1ed5a8cbf3394210      ← 单段上传，等于内容 MD5
```

分段上传的 ETag 不是内容 MD5，而是「各段 MD5 拼接后再取 MD5」加上 `-段数` 后缀。**同一份
内容换个分段大小上传就得到不同 ETag。**

实际后果：运维换个上传工具或调了 `part_size`，整桶 ETag 全变，下次同步把**所有对象**判成
已更新，触发全量重新解析与重新 embedding。不丢数据，但白烧一整轮算力。

### 决策：接受 ETag 作为 version，把契约收紧为单向保证

`version` 的契约改为：

> **内容变了，version 一定变。反向不保证——version 变了内容未必变，取决于连接器。**

理由是**误判方向是安全的**：

- ETag 变了但内容没变 → 多做一次索引，浪费算力，**结果正确**
- 「内容变了但 ETag 不变」**不会发生** → 同一分段配置下 ETag 是内容的确定性函数

契约里真正要命的那一半（漏掉真实变更，导致检索到过期内容）依然成立。本地目录的内容
SHA-256 实现继续满足更强的双向保证，收紧契约不影响它。

**不选的两条路及理由**：

- **下载内容自己算哈希**：契约完美成立，但每次同步要下载整个桶。这等于把 S3 退化成
  「远程的本地目录」，丢掉了「列举响应直接带校验值」这个最大优势，网络成本不成比例。
- **先比 (size, ETag)，ETag 变而 size 不变时下载校验**：比上一条省，但把 size 请回了
  判定链路——而 V5-6 明确排除 size，因为编辑后大小恰好不变的情况真实存在。为了「重传」
  这一个场景引入一个已被否决的判据，不划算。

已知边界写进文档：**换分段大小重传会触发该对象重新索引**。

## 3. 大小限制：超限对象跳过

**这是 V5-7 的验收前提，不是增强。** `Connector.fetch()` 返回 `bytes`，整个文件进内存；
`data_source_sync` 走的是 `index_document`，**绕过了 API 上传路径的 `validate_upload`**
（`max_upload_mb` 默认 15）。桶里一个 2GB 的对象会直接把 Worker 打死，而对象存储里放
大文件非常常见。

- **在列举阶段跳过，不下载。** `list_objects` 本来就带回 `size`，超限对象在列举时就被
  过滤掉，根本不拉取。
- **记为「跳过」而非「失败」**：差异计算完全看不到它——不入队、不软删、不影响熔断分母。
  同步结果里报告跳过清单与数量，**并且每个被跳过的对象在日志里留一条
  `data_source.object_skipped`**。后半句是端到端演练时补的：`IndexWorker` 调用
  `run_sync` 并不接返回值，而超限对象不进任何一张表，光有返回值字段等于运维查不到
  ——「同步成功但这份文档搜不到」会变成无从下手的问题。Worker 进程原先也没有任何
  logging 配置，INFO 记录会被 lastResort 的 WARNING 门槛丢掉，两处都要补。
- **上限复用 `max_upload_mb`，不新增配置项。** 真正的约束是**处理能力**（解析与 embedding
  的内存和时间），而它对两条路径完全相同：一份 50MB 的 PDF，无论来自浏览器还是 S3，
  代价一模一样。「对象存储里文件更大」不构成放宽理由——来源变了，处理成本没变。真要放宽
  应该整体调高 `max_upload_mb`，让上传与同步一起生效。
- **本地目录连接器同样按 size 跳过。** 两个实现对同一份配置必须有相同行为，不能只在
  S3 侧做。这是本阶段对 V5-6 的一处补正。

为什么不当作「失败」：V5-6 加的失败重试机制会让失败对象每次同步都重试一次，而超限对象
每次必然失败，等于每轮都白下载一遍那个大文件。跳过则完全不碰它。

## 4. S3 连接器

配置（`data_sources.configuration`）：

```json
{
  "endpoint": "s3.example.com",
  "bucket": "enterprise-docs",
  "prefix": "handbook/",
  "region": "cn-north-1",
  "secure": true,
  "credential_env": "ENTERPRISE_DOCS"
}
```

- `key` = 去掉 `prefix` 之后的 object key，保留 `/`，与本地目录一致。
- `version` = 服务端返回的 ETag。minio SDK 已剥离引号（实测返回
  `'40820467919c684a8c89388304bcd584-3'`），不需要自己处理。
- 列举用 `list_objects(bucket, prefix=..., recursive=True)`，**分页由 SDK 内部消化**，
  正好匹配协议「返回迭代器，框架层看不到分页」。跳过 `is_dir` 为真的条目。
- `fetch` 用 `get_object`，它返回的是 HTTP 响应而非字节；**必须 `close()` 且
  `release_conn()`**，否则连接池泄漏。用 `try/finally` 包住。

`source_type` 增加 `object_storage`——该取值自 `0001` 起就预留着，本阶段起它才对应真实实现。

## 5. 凭据边界

**凭据绝不进数据库。** `configuration` 只存上面那些非敏感项，访问密钥从环境变量
`{credential_env}_ACCESS_KEY` 与 `{credential_env}_SECRET_KEY` 读取。

依据是项目已有的硬边界：审计链不记录密钥（`audit.py`），备份工具主动拒绝
`.key` / `.pem` / `credentials.json`（`backup_restore.py`），密钥统一走 `.env`。把凭据写进
`configuration` 会让数据库备份、审计 payload 和只读数据源接口同时变成密钥泄露面。
副作用是凭据轮换只改环境变量，不动数据库。

缺少对应环境变量时以 `SOURCE_CREDENTIALS_MISSING` 失败，**不回退匿名访问**——回退会让
一个配置错误表现成「桶是空的」，而空清单会被差异计算判成全部删除。

## 6. 错误映射

S3 的错误码映射为项目的稳定错误码（错误码经实测确认）：

| S3Error.code | 映射 | 说明 |
| --- | --- | --- |
| `NoSuchBucket` | `SOURCE_ROOT_UNAVAILABLE` | 与本地目录的根目录不存在同义——都是「数据源整体不可达」，绝不能退化成空清单 |
| `InvalidAccessKeyId` / `SignatureDoesNotMatch` | `SOURCE_CREDENTIALS_INVALID` | 与「未配置凭据」区分：一个是没给，一个是给错了 |
| `NoSuchKey` | `SOURCE_OBJECT_MISSING` | 列举与拉取之间对象被删，可预期状态 |
| 其余 S3Error | `SOURCE_UNAVAILABLE` | 网络、超时、限流等，同步整体失败并保留原始 code 于失败原因 |

`SOURCE_ROOT_UNAVAILABLE` 的复用是有意的：同步框架已经据此拒绝「把不可达当成全部删除」，
S3 侧不该另造一套语义。

## 7. 协议是否需要改

**不改。** 第二个实现验证下来，`list_objects` + `fetch` 两个方法足够，S3 的分页在 SDK 内部
消化，不需要往协议里加任何东西。

一处**曾经担心但证明不必改**的地方：V5-6 的 spec 写了「`list_objects` 可能很贵，协议不掩盖
这一点」，当时是因为本地目录要读全文件算哈希。S3 恰好相反——一次 API 调用就带回全部 ETag。
两种实现成本差几个数量级，但协议不需要为此增加「便宜的预检」方法，调用方每轮同步只调
一次，这个差异不影响用法。抽象在这一点上是对的。

唯一的调整是 `SourceObject.version` 的**契约文字**（第 2 节），不是接口形状。

## 8. 依赖

引入 `minio` 官方 SDK（实测 7.2.20）。

不走手写 SigV4 的零依赖路线（`lexical.py` 为了不加依赖手写了 BM25）。区别在于失败模式：
BM25 写错是指标掉几个点，看得见也测得出；**SigV4 签名写错只返回 403，不指出哪一步错**，
且没有第二个实现可以对照，调试成本极高。这不是能体现工程判断的地方。

不选 `boto3`：只用 ListObjectsV2 与 GetObject 两个调用，botocore 的依赖树代价不成比例。

## 9. 非目标

- **不做分批同步。** 长任务的队列堵塞与租约超时（`index_job_stale_seconds` 默认 900 秒）
  是**生产规模问题**，而 #92 明确本阶段「不实施云生产、高可用、正式 SLO」。V5-7 的验收用
  MinIO 桶里几十个对象、秒级完成，碰不到租约。触发条件与将来方案记入第 11 节已知边界。
- 不做定时同步、webhook、事件驱动（沿用 V5-6 的判断）。
- 不做跨桶、不做多前缀通配。一个数据源 = 一个桶 + 一个前缀。
- 不做对象版本（S3 versioning）感知，只同步当前版本。
- 不实现第三个连接器（GitHub / Confluence / web）。
- 不做硬删除；不改 V2/V3 的检索与回答算法基线。

## 10. 测试

| 文件 | 覆盖 |
| --- | --- |
| `backend/tests/test_connectors.py`（扩） | S3 连接器契约：ETag 作为 version；`prefix` 剥离后 key 保留子目录；`is_dir` 条目被跳过；超限对象在列举阶段被跳过且**不触发下载**；缺失凭据、桶不存在、凭据错误、对象缺失四种错误映射 |
| `backend/tests/test_s3_sync.py`（新增，需 MinIO） | 端到端：首次全量；无变化空跑零索引任务；新增；内容更新（ETag 变）；删除软删；熔断；超限对象全程不参与差异 |
| `backend/tests/test_sync_pipeline.py`（扩） | 本地目录连接器同样按 size 跳过超限对象（第 3 节的补正）；跳过的对象在日志里留下带 `object_key` 与 `size_bytes` 的记录 |
| `backend/tests/test_postgres_foundation.py`（扩） | `object_storage` 取值可插入（`source_type` CHECK 不需要改迁移——该取值 `0001` 就有） |

CI 在 `pytest.yml` 增加 minio service，方式与既有 postgres service 一致。**这一步能进 CI
是选 S3/MinIO 而非 GitHub 的核心理由**——GitHub 连接器需要 token 与网络，在 CI 里跑不跑得
起来取决于 secrets 配置，很容易变成下一处无人发现的腐烂。

**分段上传的 ETag 行为要有专门测试**：用两种 `part_size` 上传同一内容，断言 ETag 不同
且段数后缀符合预期。这条固化的是第 2 节那个有意接受的代价，防止后人误以为是 bug 而
「修」掉。它放在连接器层——端到端组合验证需要十几 MB 的上传与索引，而它只是两个
已被各自覆盖的事实的组合。

## 11. 已知边界

- **换分段大小重传会触发该对象重新索引。** ETag 是分段配置的函数，这是接受 ETag 作为
  version 的代价（第 2 节）。
- **超限对象被跳过**，只在 Worker 日志的 `data_source.object_skipped` 里报告，不在文档
  列表里出现——它从未被索引过。查询方式写进了运行手册。
- **未做分批**：单次同步在超大桶上可能长时间占用 worker（单 worker 串行），并可能超过
  `index_job_stale_seconds`（默认 900 秒）导致租约被回收、滚动更新期间重复领取。
  将来的方案是每批处理 N 个对象后重新入队自己继续；届时不需要游标或新表，因为
  `data_source_objects` 的记录本身就是断点。
- 不感知 S3 对象版本控制，桶开了 versioning 也只同步当前版本。

## 12. 验收

- MinIO 桶里放 5 个对象 → 首次同步全部索引 → 不改动再同步产生零索引任务 → 改一个对象
  内容 → 只重建那一个 → 删两个 → 软删两个且检索不到 → 恢复其中一个 → 检索恢复。
- 分段上传验证：同一内容以两种 `part_size` 上传，ETag 不同且段数后缀分别为 `-3` / `-2`。
  这一条在**连接器层**验证。「version 变化触发重新索引」由同步层的改内容场景覆盖，
  两段合起来即完整链路；端到端再组合一次需要上传十几 MB 并真索引几千个分块，
  成本不抵价值。
- 超限验证：桶里放一个超过 `max_upload_mb` 的对象，同步跳过它、不下载、不入队、不影响
  熔断分母，且同步整体 `succeeded`。
- 凭据验证：环境变量缺失以 `SOURCE_CREDENTIALS_MISSING` 失败、给错以
  `SOURCE_CREDENTIALS_INVALID` 失败，两者都不回退匿名访问、不触发任何删除。
- 桶不存在时以 `SOURCE_ROOT_UNAVAILABLE` 失败，不产出空清单、不触发任何删除。
- 后端测试与 Ruff、前端测试与构建、容器质量门全部通过；CI 的 minio service 真实跑起来。
- 文档如实记录已实现范围与第 11 节的全部边界，特别是「换分段大小会触发重新索引」这条
  不得省略。
