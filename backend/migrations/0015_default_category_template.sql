CREATE TABLE category_templates (
    template_id text PRIMARY KEY,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    is_default boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX category_templates_single_active_default_idx
    ON category_templates (is_default)
    WHERE is_default AND active;

CREATE TABLE category_template_items (
    template_item_id text PRIMARY KEY,
    template_id text NOT NULL REFERENCES category_templates(template_id) ON DELETE CASCADE,
    name text NOT NULL,
    normalized_name text NOT NULL,
    description text NOT NULL DEFAULT '',
    sort_order integer NOT NULL DEFAULT 0,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (template_id, normalized_name),
    CHECK (normalized_name <> '未分类')
);

CREATE INDEX category_template_items_order_idx
    ON category_template_items (template_id, active DESC, sort_order, normalized_name);

INSERT INTO category_templates
    (template_id, name, description, is_default, active)
VALUES
    ('category_template_default', '默认分类模板', '创建知识库时复制的通用企业分类。', true, true)
ON CONFLICT (template_id) DO NOTHING;

INSERT INTO category_template_items
    (template_item_id, template_id, name, normalized_name, description, sort_order, active)
VALUES
    ('cti_product', 'category_template_default', '产品资料', '产品资料', '产品介绍、规格与方案资料', 100, true),
    ('cti_technical', 'category_template_default', '技术文档', '技术文档', '架构、接口与研发技术资料', 200, true),
    ('cti_manual', 'category_template_default', '操作手册', '操作手册', '用户与管理员操作说明', 300, true),
    ('cti_operations', 'category_template_default', '运维文档', '运维文档', '部署、监控与故障处理资料', 400, true),
    ('cti_policy', 'category_template_default', '制度规范', '制度规范', '制度、流程与合规规范', 500, true),
    ('cti_faq', 'category_template_default', '常见问题', '常见问题', '常见问题与标准解答', 600, true)
ON CONFLICT (template_id, normalized_name) DO NOTHING;
