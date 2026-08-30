"""自动分类的结果状态与失败治理。

分类失败必须落在稳定错误码上，而不是笼统的 failed：运维要据此判断「等一等会自己好」
还是「必须有人去改配置」。前者自动重试，后者重试多少次都是同样的失败。
"""

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


class RaisingGenerator(FakeGenerator):
    def __init__(self, error: Exception):
        super().__init__("", ready=True)
        self.error = error

    def generate(self, _prompt: str):
        raise self.error


CATEGORIES = [
    {
        "category_id": "cat_1234567890abcdef",
        "name": "技术文档",
        "description": "系统设计",
        "active": True,
        "is_system": False,
    }
]
INACTIVE_CATEGORIES = CATEGORIES + [
    {
        "category_id": "cat_fedcba0987654321",
        "name": "已停用",
        "description": "",
        "active": False,
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
    assert result.failure_code is None


def test_low_confidence_classification_requires_review() -> None:
    """低置信不写分类，只留建议——猜错的分类比没有分类更难发现。"""

    result = DocumentClassifier(
        FakeGenerator(
            '{"category_id":"cat_1234567890abcdef","confidence":0.61,"reason":"信息不足"}'
        )
    ).classify("notes.md", "少量内容", CATEGORIES)
    assert result.status == "review_required"
    assert result.category_id == "cat_1234567890abcdef", "建议分类要保留，供人工一键确认"
    assert result.failure_code is None


def test_invalid_response_is_not_retryable() -> None:
    """模型格式不对，重试只会再拿到一份不对的格式。"""

    result = DocumentClassifier(FakeGenerator("invalid")).classify(
        "notes.md", "内容", CATEGORIES
    )
    assert result.status == "failed"
    assert result.category_id is None
    assert result.failure_code == "INVALID_RESPONSE"
    assert result.retryable is False


def test_model_unavailable_is_retryable() -> None:
    result = DocumentClassifier(FakeGenerator("unused", ready=False)).classify(
        "notes.md", "内容", CATEGORIES
    )
    assert result.failure_code == "MODEL_UNAVAILABLE"
    assert result.retryable is True


def test_model_timeout_is_retryable() -> None:
    result = DocumentClassifier(RaisingGenerator(TimeoutError("30s"))).classify(
        "notes.md", "内容", CATEGORIES
    )
    assert result.failure_code == "MODEL_TIMEOUT"
    assert result.retryable is True


def test_unexpected_generator_error_is_retryable() -> None:
    """没预料到的异常按可重试处理：真是偶发就自己好了，真是必然失败会耗尽重试次数。"""

    result = DocumentClassifier(RaisingGenerator(ConnectionResetError("peer"))).classify(
        "notes.md", "内容", CATEGORIES
    )
    assert result.failure_code == "UNKNOWN_ERROR"
    assert result.retryable is True


def test_unknown_category_id_is_reported_separately() -> None:
    """模型编了一个不存在的分类，与「响应格式不对」是两回事，处置也不同。"""

    result = DocumentClassifier(
        FakeGenerator('{"category_id":"cat_0000000000000000","confidence":0.95,"reason":"猜的"}')
    ).classify("notes.md", "内容", CATEGORIES)
    assert result.failure_code == "CATEGORY_NOT_FOUND"
    assert result.retryable is False


def test_inactive_category_is_reported_separately() -> None:
    """选中的分类被停用了：这是配置问题，要么启用它，要么人工改归属。"""

    result = DocumentClassifier(
        FakeGenerator('{"category_id":"cat_fedcba0987654321","confidence":0.95,"reason":"停用"}')
    ).classify("notes.md", "内容", INACTIVE_CATEGORIES)
    assert result.failure_code == "CATEGORY_INACTIVE"
    assert result.retryable is False


def test_no_active_category_is_a_failure_not_a_pending_state() -> None:
    """没有任何可用分类是配置问题，必须显式失败。

    此前它返回 pending，与「排队等分类」一模一样——于是知识库分类字典是空的这件事
    永远不会有人发现，资料就一直「待分类」下去。
    """

    result = DocumentClassifier(FakeGenerator("unused")).classify("notes.md", "内容", [])
    assert result.status == "failed"
    assert result.failure_code == "NO_ACTIVE_CATEGORY"
    assert result.retryable is False
