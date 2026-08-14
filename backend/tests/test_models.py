from backend.app import models


def test_embedding_model_initializes_lazily(monkeypatch):
    created = []

    class FakeSentenceTransformer:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

        def encode(self, texts, **_kwargs):
            return type("Vectors", (), {"tolist": lambda self: [[1.0] for _ in texts]})()

    monkeypatch.setattr(models, "SentenceTransformer", FakeSentenceTransformer)
    embedder = models.EmbeddingModel("local-embedding")
    assert created == []
    assert embedder.encode(["测试"]) == [[1.0]]
    assert created[0][1] == {"device": "cpu", "model_kwargs": {"low_cpu_mem_usage": False}}


def test_reranker_initializes_only_when_chunks_exist(monkeypatch):
    created = []

    class FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

        def predict(self, pairs):
            return [0.8 for _ in pairs]

    monkeypatch.setattr(models, "CrossEncoder", FakeCrossEncoder)
    reranker = models.Reranker("local-reranker")
    assert reranker.score("问题", []) == []
    assert created == []
    assert reranker.score("问题", ["证据"]) == [0.8]
    assert created[0][1] == {"device": "cpu", "model_kwargs": {"low_cpu_mem_usage": False}}


def test_demo_lexical_reranker_does_not_load_cross_encoder(monkeypatch):
    def fail_if_created(*_args, **_kwargs):
        raise AssertionError("Demo 词法精排不应加载 CrossEncoder")

    monkeypatch.setattr(models, "CrossEncoder", fail_if_created)
    reranker = models.Reranker(models.DEMO_LEXICAL_RERANKER)

    scores = reranker.score("回答如何追溯来源", ["无关内容", "回答可以追溯到来源文件"])

    assert scores[1] > scores[0]
