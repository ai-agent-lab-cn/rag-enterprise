import pytest

from backend.app.errors import AppError
from backend.app.parsers import parse_document


def test_markdown_paragraphs_are_numbered() -> None:
    sections = parse_document("notes.md", "第一段\n\n第二段".encode())
    assert [section.paragraph for section in sections] == [0, 1]
    assert sections[1].text == "第二段"


def test_fenced_code_block_is_not_split_by_its_blank_lines() -> None:
    """代码块内部的空行不得把一段命令拦腰截断成两个不完整的分块。"""

    raw = "## 创建与校验\n\n```bash\n第一条命令\n\n第二条命令\n```\n"
    sections = parse_document("guide.md", raw.encode())

    assert len(sections) == 1
    assert "第一条命令" in sections[0].text
    assert "第二条命令" in sections[0].text


def test_code_block_is_attached_to_the_preceding_context() -> None:
    """独立成段的命令没有任何自然语言描述，向量检索几乎不可能命中它。"""

    raw = "先执行备份，再校验产物。\n\n```bash\nbackup --verify\n```\n\n随后归档。\n"
    sections = parse_document("guide.md", raw.encode())

    assert len(sections) == 2
    assert sections[0].text.startswith("先执行备份")
    assert "backup --verify" in sections[0].text
    assert sections[1].text == "随后归档。"


def test_leading_code_block_without_context_stays_standalone() -> None:
    raw = "```bash\nsetup\n```\n\n后续说明。\n"
    sections = parse_document("guide.md", raw.encode())

    assert len(sections) == 2
    assert sections[0].text.startswith("```bash")


def test_long_list_paragraph_is_split_per_item() -> None:
    """一个段落塞进多个要点，只会得到一个被稀释的向量。"""

    items = "\n".join(f"- 第 {index} 条要点，" + "补足长度的描述文字。" * 3 for index in range(1, 6))
    sections = parse_document("guide.md", f"以下规则：\n\n{items}\n".encode())

    assert len(sections) == 6
    assert sections[0].text == "以下规则："
    assert sections[1].text.startswith("- 第 1 条要点")
    assert sections[5].text.startswith("- 第 5 条要点")


def test_numbered_list_is_split_and_keeps_continuation_lines() -> None:
    raw = (
        "1. 第一步，" + "描述文字。" * 16 + "\n"
        "2. 第二步，" + "描述文字。" * 16 + "\n"
        "   这一行是第二步的续行。\n"
        "3. 第三步，" + "描述文字。" * 16 + "\n"
    )
    sections = parse_document("guide.md", raw.encode())

    assert len(sections) == 3
    assert "这一行是第二步的续行。" in sections[1].text


def test_short_list_is_not_split() -> None:
    """条目少或篇幅短的列表拆开反而丢失上下文。"""

    sections = parse_document("guide.md", "- 甲\n- 乙\n- 丙\n".encode())

    assert len(sections) == 1


def test_list_containing_code_block_is_not_split() -> None:
    """代码块刚被并入上下文，不能在这里又被按行切碎。"""

    raw = (
        "- 第一条要点，" + "描述文字。" * 8 + "\n"
        "- 第二条要点，" + "描述文字。" * 8 + "\n"
        "- 第三条要点，" + "描述文字。" * 8 + "\n\n"
        "```bash\nrun --now\n```\n"
    )
    sections = parse_document("guide.md", raw.encode())

    assert len(sections) == 1
    assert "run --now" in sections[0].text


@pytest.mark.parametrize("filename", ["empty.md", "empty.txt"])
def test_empty_file_is_rejected(filename: str) -> None:
    with pytest.raises(AppError) as error:
        parse_document(filename, b"")
    assert error.value.code == "EMPTY_FILE"


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(AppError) as error:
        parse_document("bad.txt", b"\xff")
    assert error.value.code == "INVALID_ENCODING"
