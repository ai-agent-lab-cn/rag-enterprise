-- 记录索引实际使用的向量模型。Chroma 侧在 collection metadata 上有等价校验，
-- PostgreSQL 侧此前没有：chunks.embedding 是无维度约束的 vector，换模型后新旧维度
-- 混存，直到检索执行 <=> 时才会报错，且届时索引已被污染。
CREATE TABLE index_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    embedding_model text NOT NULL,
    embedding_dimension integer NOT NULL CHECK (embedding_dimension > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
