"""确定性查询规范化与有限扩展，不调用生成模型。"""

import re
import unicodedata
from dataclasses import dataclass

MAX_EXPANDED_QUERIES = 3

_ALIASES: dict[str, tuple[str, ...]] = {
    "acl": ("访问控制", "权限隔离"),
    "api": ("应用程序接口",),
    "rag": ("检索增强生成",),
    "slo": ("服务等级目标",),
    "sso": ("单点登录",),
}
_QUOTED_PHRASE = re.compile(r'["“”]([^"“”]{2,80})["“”]')
_IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9._/-]*\d)[A-Za-z][A-Za-z0-9._/-]{1,63}\b")
_ABBREVIATION = re.compile(r"\b[A-Za-z][A-Za-z0-9]{1,9}\b")


@dataclass(frozen=True)
class QueryPlan:
    original: str
    normalized: str
    queries: tuple[str, ...]
    strategy: str

    @property
    def expansion_count(self) -> int:
        return max(0, len(self.queries) - 1)


def build_query_plan(question: str) -> QueryPlan:
    """返回稳定、有上限且不包含自由生成内容的查询计划。"""

    normalized = normalize_query(question)
    expansions: list[str] = []

    for phrase in _QUOTED_PHRASE.findall(normalized):
        _append_unique(expansions, normalize_query(phrase), normalized)
    for identifier in _IDENTIFIER.findall(normalized):
        _append_unique(expansions, identifier, normalized)
    for token in _ABBREVIATION.findall(normalized):
        for alias in _ALIASES.get(token.casefold(), ()):
            _append_unique(expansions, f"{token.upper()} {alias}", normalized)

    queries = (normalized, *expansions[:MAX_EXPANDED_QUERIES])
    if len(queries) > 1:
        strategy = "controlled_expansion"
    elif normalized != question.strip():
        strategy = "normalized"
    else:
        strategy = "original"
    return QueryPlan(question, normalized, queries, strategy)


def normalize_query(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question)
    normalized = normalized.replace("“", '"').replace("”", '"')
    return " ".join(normalized.split()).strip()


def _append_unique(items: list[str], value: str, original: str) -> None:
    if value and value.casefold() != original.casefold() and all(
        value.casefold() != item.casefold() for item in items
    ):
        items.append(value)
