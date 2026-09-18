-- 正式检索评测从「离线跑完再把文件放进仓库」变成产品内的可靠队列。
--
-- 此前 evaluation_runs 只有一处写入（postgres_evaluation.run_acceptance），记录的是
-- 验收结论；候选索引版本要拿到一份可用于三层门禁的正式检索报告，只能由人在命令行跑
-- run_corpus_baseline 再把 JSON 提交进 backend/evaluation/reports。结果是页面上「执行
-- 三层验证」永远选不到匹配当前配置指纹的报告，而弹框除了让用户自己去跑评测之外给不出
-- 任何入口。这次把这张表补成运行事实 + 队列：入队即冻结候选版本、配置指纹、数据集与
-- 基线报告，由独立 Evaluation Worker 领取执行。
--
-- 为什么不新建一张表：evaluation_runs 已经有 index_version_id 外键、metrics、official
-- 与 passed 两列，正式评测运行本来就该落在这里。新建表会出现两套「评测跑过没有」的真相。
--
-- 存量数据：0013 建表时没有 status 概念，所有已存在的行都是跑完之后才写入的，
-- 因此回填 succeeded 是可判定的事实而不是猜测；finished_at 用 run_at，updated_at 用
-- created_at，都取自同一行已有的时间，不引入 now()。

-- 1) 队列与运行事实列。NOT NULL 一律带 DEFAULT，否则存量行会被拦住。
ALTER TABLE evaluation_runs ADD COLUMN status text NOT NULL DEFAULT 'succeeded';
ALTER TABLE evaluation_runs ADD COLUMN operation_id text
    REFERENCES operations(operation_id) ON DELETE SET NULL;
ALTER TABLE evaluation_runs ADD COLUMN config_fingerprint text;
ALTER TABLE evaluation_runs ADD COLUMN baseline_report_id text;
ALTER TABLE evaluation_runs ADD COLUMN report_payload jsonb;
ALTER TABLE evaluation_runs ADD COLUMN requested_by text;
ALTER TABLE evaluation_runs ADD COLUMN attempt_count integer NOT NULL DEFAULT 0;
ALTER TABLE evaluation_runs ADD COLUMN max_attempts integer NOT NULL DEFAULT 3;
ALTER TABLE evaluation_runs ADD COLUMN available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE evaluation_runs ADD COLUMN locked_at timestamptz;
ALTER TABLE evaluation_runs ADD COLUMN locked_by text;
ALTER TABLE evaluation_runs ADD COLUMN error_code text;
ALTER TABLE evaluation_runs ADD COLUMN error_message text;
ALTER TABLE evaluation_runs ADD COLUMN started_at timestamptz;
ALTER TABLE evaluation_runs ADD COLUMN finished_at timestamptz;
ALTER TABLE evaluation_runs ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- 2) 回填存量行，再收窄可空性。顺序反了 passed 那一列的语义会先撒谎一次。
UPDATE evaluation_runs
SET finished_at=run_at, updated_at=created_at
WHERE finished_at IS NULL;

-- queued 的运行还没有阈值结论。passed=false 表示「跑完了没达标」，
-- 与「还没跑」是两件事，不能用同一个值表达。
ALTER TABLE evaluation_runs ALTER COLUMN passed DROP NOT NULL;

-- 3) 域约束。
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_status_check
    CHECK (status IN ('queued','running','succeeded','failed','cancelled'));
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_config_fingerprint_check
    CHECK (config_fingerprint IS NULL OR config_fingerprint ~ '^[a-f0-9]{64}$');
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_attempt_count_check
    CHECK (attempt_count >= 0);
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_max_attempts_check
    CHECK (max_attempts > 0);
-- 终态必须说得出结论或原因：succeeded 要有报告，failed 要有错误码。
-- 只约束 retrieval：验收类运行的证据在 metrics 列里，从来没有 report_payload，
-- 把它们一起纳进来会让这条迁移被存量行直接拦住。
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_terminal_evidence_check
    CHECK (
        evaluation_type <> 'retrieval'
        OR (
            (status <> 'succeeded' OR (report_payload IS NOT NULL AND passed IS NOT NULL))
            AND (status <> 'failed' OR error_code IS NOT NULL)
        )
    );

-- 4) 原表级 UNIQUE (evaluation_type, dataset_id, dataset_version, commit_sha, run_at)
-- 把 run_at 当成幂等键的一部分。队列语义下这条约束只会咬人：入队时还没有运行时间，
-- 收口时才对齐报告时间，而报告 ID 只到秒级精度，同一秒完成的两次运行会被判成重复。
-- retrieval 的真正并发约束是「同一候选版本只允许一个未完成运行」，由下面的部分唯一
-- 索引表达；其余评测类型保持原语义不变。
-- 约束名由 Postgres 自动生成并截断到 63 字符，不能凭猜写死。
DO $$
DECLARE
    unique_name text;
BEGIN
    SELECT conname INTO unique_name
    FROM pg_constraint
    WHERE conrelid='evaluation_runs'::regclass AND contype='u';
    IF unique_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE evaluation_runs DROP CONSTRAINT %I', unique_name);
    END IF;
END $$;

CREATE UNIQUE INDEX evaluation_runs_offline_identity_idx
    ON evaluation_runs (evaluation_type, dataset_id, dataset_version, commit_sha, run_at)
    WHERE evaluation_type <> 'retrieval';

CREATE UNIQUE INDEX evaluation_runs_one_active_retrieval_idx
    ON evaluation_runs (index_version_id)
    WHERE evaluation_type='retrieval'
      AND index_version_id IS NOT NULL
      AND status IN ('queued','running');

-- 领取查询按 (evaluation_type, status, available_at, created_at) 过滤与排序。
CREATE INDEX evaluation_runs_claim_idx
    ON evaluation_runs (evaluation_type, status, available_at, created_at);
CREATE INDEX evaluation_runs_version_idx
    ON evaluation_runs (index_version_id, created_at DESC)
    WHERE index_version_id IS NOT NULL;

-- 5) 正式评测是长任务，需要面向用户的进度投影。0036 删掉 index_validation /
-- index_activation 的理由是「验证与激活是单事务动作，没有进度可跟踪」——那条理由对
-- 评测不成立：一次正式评测要建语料、跑召回、跑精排、算指标，阶段是真实存在的。
ALTER TABLE operations DROP CONSTRAINT IF EXISTS operations_operation_type_check;
ALTER TABLE operations ADD CONSTRAINT operations_operation_type_check
    CHECK (operation_type IN (
        'index_build', 'index_evaluation', 'sync_run',
        'file_upload', 'file_update', 'document_reprocess'
    ));
