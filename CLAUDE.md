# RAG Enterprise 项目规则

只写这个仓库真实踩过的坑。每条都附实例——没有实例的规则不要往里加，那是正确的废话，
只会稀释真正有用的部分。

## 一、控件禁用了，就必须说得出为什么

`disabled` 的按钮在页面上是一个点不动、也不解释自己的灰色方块。用户看到的不是「这里
有条件没满足」，而是「这个功能坏了」。

- 禁用原因必须**可见**，不能只放 `title`——原生 tooltip 要悬停约一秒才出现，用户根本
  不知道有它，触屏上完全看不到。
- 禁用条件与原因文案必须**同一个来源**。曾经禁用看后端 `allowed_actions`、原因文案由
  前端自己重算三个条件，两套判断一旦分叉就会出现「禁用了却说不出原因」。
  见 `KnowledgeBasesPage.tsx` 的 `deleteBlockReason()`：它同时决定 `disabled` 和文案，
  并且末尾留了兜底文案——宁可说得笼统，也不能什么都不说。
- 能用「点击后报错」代替禁用时，优先报错。空表单点提交后看到「请输入分类名称」，比
  一个灰按钮清楚得多。

## 二、同一个操作在不同页面必须行为一致

用户在一处学会的操作方式，会带到另一处。行为相反 = 功能坏了。

实例：同样文案的「新建分类」，分类模板弹框里空名称可点击并报错，知识库详情里却是
禁用且无提示。用户在模板那边习惯了能点，到这边发现点不动。

改动一个交互模式前，先 grep 同名操作在别处怎么做的。

## 三、写入路径和读取路径必须成对验证

它们通常是两处代码，只测一头就会出现「库里有值、接口返回 null」。

实例：`_classify()` 把 `classification_failure_code` 写进 metadata，而
`list_documents()` 手工逐个挑字段构造响应，漏挑了它。写入测试全绿，但接口一直返回
`null`——直到端到端验收才发现。

`list_documents` 这类手工字段映射尤其危险：**新增 schema 字段时它不会报错，只会静默
返回默认值**。

## 四、双实现只测一个等于没测

`AuthRepository`（JSON）与 `PostgresAuthRepository` 是同一套规则的两份实现，生产跑的是
后者。而 `test_auth.py` 测的是前者。

结果：`PostgresAuthRepository.update_user` 在 `dict_row` 连接上写了 `fetchone()[0]`，
**改任何管理员的角色或启用状态都抛 KeyError**，「至少保留一名管理员」的保护从未真正
生效过——它只是碰巧被异常挡住。而 JSON 版两个场景都正确。

有两份实现时，测**生产用的那份**；两份都要保证行为，就写同一组断言跑两遍
（见 `test_postgres_update_user_matches_the_json_implementation`）。

## 五、没有 CI 覆盖的东西会静默腐烂

这个仓库已经出现过五次：

| 腐烂物 | 表现 |
| --- | --- |
| `run_baseline.py` | 坏了整整一个大版本，零测试 |
| `evaluate.py` | 缺目录时静默建空集合，所有指标 0.000 且退出码 0 |
| K8s schema 版本 | 落后两个版本，且 configmap 与 workloads 自相矛盾 |
| `render.yaml` | 描述了一个不存在的架构 |
| `check_schema_version(url, 13)` | V13 升 V15 时漏改，4 个测试一直红着没人管 |

推论：
- 跳过（skip）在 CI 日志里长得和通过一模一样。依赖外部服务的测试，在 CI 里缺服务必须
  **失败而不是跳过**（见 `backend/tests/conftest.py` 的收集期守卫）。
- 加了新的一致性约束，就顺手加上能自动发现它被破坏的检查。

## 六、schema 版本号涨了，五个地方要一起改

`apply_migrations` 返回值不是唯一的真相来源。加迁移时同步：

1. `backend/app/config.py` 的 `required_database_schema_version`
2. `.env.example`
3. `docker-compose.yml` / `docker-compose.release.yml`
4. `deploy/kubernetes/configmap.yaml`
5. `deploy/kubernetes/workloads.yaml` 的 `--required-version`

`backend/tests/test_env_example.py` 会检查第 1、2 项是否一致，
`scripts/validate_kubernetes.py` 检查第 5 项。其余靠人。

## 七、前端

- **`pattern` 属性要在 `v` flag 下合法**。Chrome 125+ 用 `v` 解析它，字符类里未转义的
  `-` 是语法错误，浏览器会**静默丢弃整个 pattern**，前端校验无声失效。写
  `[A-Za-z0-9._\-]+`，不要写 `[A-Za-z0-9._-]+`。`App.test.tsx` 有一条测试扫描全部
  `pattern` 属性。
- **Node 版本下限是 `^20.19.0 || >=22.12.0`**。低于它 npm 会静默跳过平台原生 binding，
  `npm test` 报 `Cannot find native binding` 但**退出码仍是 0**。删 lockfile 重装无效
  ——原因是 engine 不匹配，不是锁文件。
  注意这个下限来自**依赖包** rolldown 与 vite 的 `engines`，`frontend/package.json`
  自己**没有声明 engines**，所以 npm 不会对本项目发出版本警告。README 第 49 行有记录。
- **类型检查必须用 `npm run typecheck`（`tsc -b`），`npx tsc --noEmit` 是空跑的**。
  根 `tsconfig.json` 是 `files: []` + project references，`--noEmit` 不会构建被引用的
  子项目——实测给它一个赤裸的类型错误，**退出码仍是 0**。而 `tsc -b`、
  `tsc -p tsconfig.app.json`、`npm run build` 都能正确报错。
- **令牌只存在页面内存**，刷新即失效。写 Playwright 脚本时不能用 `page.goto()` 做页面
  间跳转（那是整页刷新），要走页面内导航；`storageState` 也存不下它。
- **登录限流默认 10 次/窗口**（`LOGIN_RATE_LIMIT`）。自动化测试一页一登会在第 11 页
  撞上 429，共用一次登录。

## 八、视觉基线

`frontend/e2e/visual-baseline.spec.ts` 覆盖 11 个页面状态，**不进 CI**——截图绑定数据集，
放进 CI 只会因数据漂移天天误报。它是迁移期工具：改样式前后各跑一次，让每处像素变化都
必须被显式接受。

```bash
cd frontend
SMOKE_ADMIN_USERNAME=... SMOKE_ADMIN_PASSWORD=... \
  npx playwright test visual-baseline --project=desktop-chromium
# 确认变化符合预期后，加 --update-snapshots 接受新基线
```

系统状态页与审计记录页**有意排除**：前者的指标因访问本身而变化，后者只增不减，
两者变的是数据不是样式。

## 九、验证要求

改完必须真跑，附实际输出，不用「应该能过」推测：

```bash
TEST_DATABASE_URL=... MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest -q
uv run ruff check backend evaluations scripts
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```

涉及页面交互的改动，还要在真实浏览器里走一遍——组件测试通过不等于页面能用。这个仓库
里「测试全绿但功能不可用」出现过不止一次（最近一次：`reclassify` 把状态改回 pending
却不入队，用户点了按钮什么也不会发生）。
