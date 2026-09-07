-- 阶段 2：让首次索引的引导激活留下证据。
--
-- active_or_bootstrap_version 在知识库首次索引时创建版本并**直接置为 active**，绕过
-- building → validating → ready 的整条状态机。这条路径不能删：所有首次索引都走它
-- （不只是数据源同步），切断的话用户上传第一份文档后知识库将无法检索，直到有人手工
-- 跑一次评测并激活。
--
-- 但「不能删」不等于「可以查不到」。首版现在也会留下一份验证报告，来源标 bootstrap：
-- 它既不是跑过三层门禁的 standard，也不是升级回填的 legacy_backfill，而是「首次索引，
-- 没有前序基线可比」。三层结果一律 unknown——首版确实没经过门禁，写 pass 就是伪造。
--
-- 有了它，回滚到首版之后还能再切回来（switch_to_version 要求持有 pass 报告），
-- 而页面能如实说明这个版本从未经过发布门禁。
ALTER TABLE validation_reports DROP CONSTRAINT IF EXISTS validation_reports_report_source_check;
ALTER TABLE validation_reports ADD CONSTRAINT validation_reports_report_source_check
    CHECK (report_source IN ('standard', 'legacy_backfill', 'bootstrap'));
