-- 阶段 0：删除 index_definitions。
--
-- 它在 V24 建表时就是派生装饰层：ensure_index_definition 从 index_versions 反向读取，
-- 而 vector_config / keyword_config / metadata_schema / reranker_config 四个字段写的是
-- 硬编码字面量，没有任何写入口（只有一个 GET 路由），前端唯一消费点是标注“只读展示”
-- 的弹框。真正驱动构建的始终是 index_versions.config_fingerprint。
--
-- 不升级为可编辑配置源，是因为它承诺的 per-knowledge-base 配置在当前存储结构下不成立：
-- 0010 把 chunks.embedding 从无维度 vector 改成了固定维度 vector(N)，N 取自全局单例表
-- index_settings。embedding 的模型与维度是列级别的事实，全库一个值，无法按知识库分别配置。
-- per-KB 可编辑配置留到真有需求时再建，届时它是真配置源而不是第二个派生层。
ALTER TABLE index_builds DROP COLUMN IF EXISTS index_definition_id;
DROP TABLE IF EXISTS index_definitions;
