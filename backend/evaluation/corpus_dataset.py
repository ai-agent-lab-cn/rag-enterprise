"""语料级检索评测集：标注锚定原始段落，使解析与切分进入被测范围。

与 1.0.0 的差别在于评测输入不再是写死的 chunk 列表，而是冻结的原始文档。
每次评测都重新执行 ``parse_document`` 和 ``split_sections``，因此切分参数或
解析实现的任何变化都会体现在指标上。
"""

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from backend.app.parsers import parse_document

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class CorpusDocument(BaseModel):
    """冻结语料中的一篇文档；摘要与段落数共同锁定解析结果。"""

    filename: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    paragraph_count: int = Field(ge=1)


class RelevantParagraph(BaseModel):
    """标注位置以段落为单位，因此不随 chunk_size 变化而失效。"""

    filename: str = Field(min_length=1)
    paragraph: int = Field(ge=0)


class CorpusQuery(BaseModel):
    # 沿用 1.0.0 的 query_id 形态，使两套数据集可以共用同一组检索指标函数。
    query_id: str = Field(pattern=r"^q\d{3}$")
    question: str = Field(min_length=2)
    relevant: list[RelevantParagraph] = Field(min_length=1)


class CorpusEvaluationDataset(BaseModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    language: str = "zh-CN"
    description: str = Field(min_length=1)
    corpus_dir: str = Field(min_length=1)
    documents: list[CorpusDocument] = Field(min_length=1)
    queries: list[CorpusQuery] = Field(min_length=100)

    @model_validator(mode="after")
    def validate_references(self) -> "CorpusEvaluationDataset":
        filenames = [document.filename for document in self.documents]
        if len(filenames) != len(set(filenames)):
            raise ValueError("语料评测集包含重复的文档名")
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("语料评测集包含重复的 query_id")
        paragraph_counts = {document.filename: document.paragraph_count for document in self.documents}
        for query in self.queries:
            positions = {(item.filename, item.paragraph) for item in query.relevant}
            if len(positions) != len(query.relevant):
                raise ValueError(f"{query.query_id} 标注了重复的段落位置")
            for item in query.relevant:
                if item.filename not in paragraph_counts:
                    raise ValueError(f"{query.query_id} 引用了不存在的文档：{item.filename}")
                if item.paragraph >= paragraph_counts[item.filename]:
                    raise ValueError(
                        f"{query.query_id} 引用了 {item.filename} 越界的段落：{item.paragraph}"
                    )
        return self


def paragraph_key(filename: str, paragraph: int) -> str:
    """把段落位置压成稳定字符串，以便复用按 ID 计算的检索指标。"""

    return f"{filename}#{paragraph}"


def load_corpus_dataset(path: Path) -> tuple[CorpusEvaluationDataset, dict[str, bytes]]:
    """读取评测集并校验语料未漂移，返回数据集与冻结的文档内容。

    校验 sha256 可发现语料被改动；校验段落数可发现解析实现变更，两者都会让
    历史基线失去可比性，因此必须在评测开始前直接失败，而不是产出可疑指标。
    """

    dataset = CorpusEvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))
    corpus_root = path.parent / dataset.corpus_dir
    contents: dict[str, bytes] = {}
    for document in dataset.documents:
        source = corpus_root / document.filename
        if not source.is_file():
            raise ValueError(f"语料文件缺失：{document.filename}")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != document.sha256:
            raise ValueError(f"语料文件已改动，历史基线不再可比：{document.filename}")
        actual_paragraphs = len(parse_document(document.filename, content))
        if actual_paragraphs != document.paragraph_count:
            raise ValueError(
                f"{document.filename} 解析出 {actual_paragraphs} 段，"
                f"评测集记录 {document.paragraph_count} 段：解析实现已变更"
            )
        contents[document.filename] = content
    return dataset, contents
