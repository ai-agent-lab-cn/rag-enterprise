"""零依赖的中文词法检索，用于补足向量召回在专有名词与标识符上的短板。

中文按字符 bigram 切分，ASCII 保留完整词元。这样 ``NodePort``、``30080``、
``FOR UPDATE SKIP LOCKED`` 这类标识符不会被分词器切碎，而它们恰好是向量召回最容易
漏掉的一类查询。选择 bigram 而非引入分词器，是为了不增加运行时依赖，也避免分词器
把上述标识符按中文习惯切开。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

BM25_K1 = 1.5
BM25_B = 0.75

_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9._/-]*")


def _is_cjk(character: str) -> bool:
    return "CJK" in unicodedata.name(character, "")


def tokenize(text: str) -> list[str]:
    """把文本切成 ASCII 词元与中文 bigram 的混合序列。

    单个中文字符不单独成词：bigram 已经能覆盖大部分查询，额外的单字会显著抬高
    高频虚词的权重。只有连续中文不足两个字符时才退化为单字，避免整段丢失。
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = _ASCII_TOKEN.findall(normalized)
    run: list[str] = []
    for character in normalized:
        if _is_cjk(character):
            run.append(character)
            continue
        tokens.extend(_bigrams(run))
        run = []
    tokens.extend(_bigrams(run))
    return tokens


def _bigrams(run: list[str]) -> list[str]:
    if not run:
        return []
    if len(run) == 1:
        return run
    return [run[index] + run[index + 1] for index in range(len(run) - 1)]


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: str
    score: float


class BM25Index:
    """内存中的 Okapi BM25 倒排索引；索引内容与向量库保持同一批分块。"""

    def __init__(self, documents: list[tuple[str, str]]):
        self.chunk_ids: list[str] = []
        self.lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for chunk_id, text in documents:
            position = len(self.chunk_ids)
            tokens = tokenize(text)
            self.chunk_ids.append(chunk_id)
            self.lengths.append(len(tokens))
            for token, frequency in Counter(tokens).items():
                self.postings.setdefault(token, []).append((position, frequency))
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0

    def search(self, question: str, limit: int) -> list[LexicalHit]:
        if limit < 1:
            raise ValueError("limit 必须大于等于 1")
        if not self.chunk_ids:
            return []
        total = len(self.chunk_ids)
        scores: dict[int, float] = {}
        for token in set(tokenize(question)):
            postings = self.postings.get(token)
            if not postings:
                continue
            # 概率式 IDF 加 1 后取对数，保证出现在多数文档中的词元不会得到负权重。
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for position, frequency in postings:
                length_ratio = (
                    self.lengths[position] / self.average_length if self.average_length else 1.0
                )
                denominator = frequency + BM25_K1 * (1 - BM25_B + BM25_B * length_ratio)
                scores[position] = scores.get(position, 0.0) + (
                    idf * frequency * (BM25_K1 + 1) / denominator
                )
        # 分数相同时按 chunk_id 排序，保证同一批数据每次运行的名次完全可复现。
        ranked = sorted(scores.items(), key=lambda item: (-item[1], self.chunk_ids[item[0]]))
        return [
            LexicalHit(chunk_id=self.chunk_ids[position], score=round(score, 6))
            for position, score in ranked[:limit]
        ]


class LexicalIndexCache:
    """按知识库缓存 BM25 索引，避免每次查询都从存储层拉回全部分块重建。

    倒排必须与向量库看到同一批分块，否则词法召回会命中已经不存在的分块。异步索引
    架构下写入发生在独立的 Worker 进程，API 进程收不到失效通知，因此每次取用都先比对
    一个廉价的分块指纹——跨进程的一致性由此自动成立，不依赖调用方记得触发失效。

    已知限制：构建期间持锁，同一知识库的并发查询会等待；缓存不设容量上限，
    知识库数量很多时内存随之增长。规模变大后应改为按知识库分锁并加入淘汰策略。
    """

    def __init__(
        self,
        loader: Callable[[str], list[tuple[str, str]]],
        fingerprint: Callable[[str], str],
    ):
        self._loader = loader
        self._fingerprint = fingerprint
        self._indexes: dict[str, tuple[str, BM25Index]] = {}
        self._lock = RLock()

    def get(self, knowledge_base_id: str) -> BM25Index:
        current = self._fingerprint(knowledge_base_id)
        with self._lock:
            cached = self._indexes.get(knowledge_base_id)
            if cached is not None and cached[0] == current:
                return cached[1]
            index = BM25Index(self._loader(knowledge_base_id))
            self._indexes[knowledge_base_id] = (current, index)
            return index

    def invalidate(self, knowledge_base_id: str) -> None:
        with self._lock:
            self._indexes.pop(knowledge_base_id, None)

    def clear(self) -> None:
        with self._lock:
            self._indexes.clear()

    def cached_knowledge_base_ids(self) -> set[str]:
        with self._lock:
            return set(self._indexes)
