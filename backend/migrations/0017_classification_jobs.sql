-- V17：把「重新分类」变成一种真正的任务。
--
-- 此前分类只在索引任务里随手跑一次，失败之后没有任何东西会再碰它：人工点「重新分类」
-- 只是把状态改回 pending，然后资料就永远停在 pending。
--
-- 走 index_jobs 队列之后，重试、退避、最大次数与租约恢复全部复用既有机制，不需要另造
-- 一套调度——这正是 classification_next_retry_at 之前无人写、无人读的原因。

ALTER TABLE index_jobs DROP CONSTRAINT index_jobs_job_type_check;
ALTER TABLE index_jobs ADD CONSTRAINT index_jobs_job_type_check
    CHECK (job_type IN ('index', 'rebuild', 'sync', 'classify'));

-- 同一份文档同时只允许一个待处理的分类任务：连点两次「重新分类」不该排两次队，
-- 那只会让同一份资料被分类两遍、白烧一次模型调用。
CREATE UNIQUE INDEX index_jobs_one_active_classify_idx
    ON index_jobs (document_version_id)
    WHERE job_type = 'classify' AND status IN ('queued', 'running');
