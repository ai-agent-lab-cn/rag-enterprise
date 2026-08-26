from backend.app.document_classifier import DocumentClassifier


class FakeGenerator:
    model_name = "test-classifier"

    def __init__(self, text: str, ready: bool = True):
        self.text = text
        self._ready = ready

    @property
    def ready(self) -> bool:
        return self._ready

    def generate(self, _prompt: str):
        return self.text, {}


CATEGORIES = [
    {
        "category_id": "cat_1234567890abcdef",
        "name": "技术文档",
        "description": "系统设计",
        "active": True,
        "is_system": False,
    }
]


def test_high_confidence_classification_is_auto_assigned() -> None:
    result = DocumentClassifier(
        FakeGenerator(
            '{"category_id":"cat_1234567890abcdef","confidence":0.91,"reason":"架构说明"}'
        )
    ).classify("README.md", "系统架构", CATEGORIES)
    assert result.status == "auto_assigned"
    assert result.category_id == "cat_1234567890abcdef"


def test_low_confidence_classification_requires_review() -> None:
    result = DocumentClassifier(
        FakeGenerator(
            '{"category_id":"cat_1234567890abcdef","confidence":0.61,"reason":"信息不足"}'
        )
    ).classify("notes.md", "少量内容", CATEGORIES)
    assert result.status == "review_required"


def test_classifier_failure_does_not_assign_category() -> None:
    result = DocumentClassifier(FakeGenerator("invalid")).classify(
        "notes.md", "内容", CATEGORIES
    )
    assert result.status == "failed"
    assert result.category_id is None


def test_no_business_category_remains_pending() -> None:
    result = DocumentClassifier(FakeGenerator("unused")).classify("notes.md", "内容", [])
    assert result.status == "pending"
