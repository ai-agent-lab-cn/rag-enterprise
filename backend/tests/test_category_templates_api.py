from datetime import UTC, datetime

from backend.app.main import get_category_templates


class CategoryTemplateStub:
    def __init__(self) -> None:
        self.items = [self._item("cti_product", "产品资料", 100)]

    @staticmethod
    def _item(item_id: str, name: str, sort_order: int, active: bool = True):
        now = datetime(2026, 8, 30, tzinfo=UTC)
        return {
            "template_item_id": item_id,
            "template_id": "category_template_default",
            "name": name,
            "description": "说明",
            "sort_order": sort_order,
            "active": active,
            "created_at": now,
            "updated_at": now,
        }

    def get_default(self):
        return {
            "template_id": "category_template_default",
            "name": "默认分类模板",
            "description": "创建知识库时复制。",
            "active": True,
            "item_count": len(self.items),
            "items": self.items,
            "created_at": datetime(2026, 8, 30, tzinfo=UTC),
            "updated_at": datetime(2026, 8, 30, tzinfo=UTC),
        }

    def create_item(self, name, description, sort_order):
        if name == "未分类":
            raise PermissionError("reserved")
        item = self._item("cti_created", name, sort_order)
        item["description"] = description
        self.items.append(item)
        return item

    def update_item(self, item_id, name, description, sort_order, active):
        if item_id == "cti_missing":
            return None
        item = self._item(item_id, name, sort_order, active)
        item["description"] = description
        return item

    def delete_item(self, item_id):
        return item_id != "cti_missing"


def test_admin_can_govern_default_category_template(client) -> None:
    client.app.dependency_overrides[get_category_templates] = lambda: CategoryTemplateStub()

    listed = client.get("/api/category-templates/default")
    created = client.post(
        "/api/category-templates/default/items",
        json={"name": "项目资料", "description": "项目交付资料", "sort_order": 700},
    )
    updated = client.put(
        "/api/category-templates/default/items/cti_product",
        json={"name": "产品文档", "description": "产品资料", "sort_order": 110, "active": False},
    )
    deleted = client.delete("/api/category-templates/default/items/cti_product")

    assert listed.status_code == 200
    assert listed.json()["item_count"] == 1
    assert created.status_code == 201
    assert created.json()["name"] == "项目资料"
    assert updated.status_code == 200
    assert updated.json()["active"] is False
    assert deleted.status_code == 204


def test_template_api_has_stable_reserved_and_not_found_errors(client) -> None:
    client.app.dependency_overrides[get_category_templates] = lambda: CategoryTemplateStub()

    reserved = client.post(
        "/api/category-templates/default/items",
        json={"name": "未分类", "description": "", "sort_order": 100},
    )
    missing = client.delete("/api/category-templates/default/items/cti_missing")

    assert reserved.status_code == 409
    assert reserved.json()["error"]["code"] == "CATEGORY_TEMPLATE_RESERVED_NAME"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CATEGORY_TEMPLATE_ITEM_NOT_FOUND"
