from datetime import UTC, datetime

from backend.app.main import get_categories


class CategoryStub:
    def list(self, knowledge_base_id: str):
        now = datetime(2026, 9, 3, tzinfo=UTC)
        return [{
            "category_id": "cat_origin",
            "knowledge_base_id": knowledge_base_id,
            "name": "产品资料",
            "description": "产品说明",
            "sort_order": 100,
            "active": True,
            "is_system": False,
            "origin_type": "template_copy",
            "document_count": 0,
            "created_at": now,
            "updated_at": now,
        }]

    def create(self, knowledge_base_id: str, name: str, description: str, sort_order: int):
        return {
            **self.list(knowledge_base_id)[0],
            "name": name,
            "description": description,
            "sort_order": sort_order,
            "origin_type": "manual",
        }


def test_category_api_exposes_read_only_origin(client) -> None:
    client.app.dependency_overrides[get_categories] = lambda: CategoryStub()

    listed = client.get("/api/knowledge-bases/kb_default/categories")
    created = client.post(
        "/api/knowledge-bases/kb_default/categories",
        json={"name": "项目资料", "origin_type": "template_copy"},
    )

    assert listed.status_code == 200
    assert listed.json()[0]["origin_type"] == "template_copy"
    assert created.status_code == 422, "初始来源由服务端决定，客户端不能伪造"
