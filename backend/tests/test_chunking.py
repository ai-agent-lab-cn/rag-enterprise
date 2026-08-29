from backend.app.chunking import split_sections, stable_document_id
from backend.app.parsers import ParsedSection


def test_stable_document_id_is_content_and_name_based() -> None:
    assert stable_document_id("a.md", b"same") == stable_document_id("a.md", b"same")
    assert stable_document_id("a.md", b"same") != stable_document_id("b.md", b"same")
    assert stable_document_id("a.md", b"same") != stable_document_id("a.md", b"other")


def test_split_preserves_metadata_and_overlap() -> None:
    chunks = split_sections(
        "doc_1",
        "notes.md",
        [ParsedSection("abcdefghij", page=2, paragraph=3)],
        chunk_size=6,
        overlap=2,
    )
    assert [chunk.text for chunk in chunks] == ["abcdef", "efghij"]
    assert chunks[0].metadata()["page"] == 2
    assert chunks[1].chunk_id == "doc_1:chunk:00001"
    assert chunks[1].char_count == 6


def test_split_preserves_table_column_location() -> None:
    chunks = split_sections(
        "doc_table",
        "data.xlsx",
        [
            ParsedSection(
                "A | B",
                None,
                0,
                sheet_name="Sheet1",
                row_start=2,
                row_end=3,
                column_start=1,
                column_end=2,
            )
        ],
        chunk_size=100,
        overlap=0,
    )

    assert chunks[0].metadata()["column_start"] == 1
    assert chunks[0].metadata()["column_end"] == 2


def test_non_default_knowledge_base_scopes_chunk_ids_and_metadata() -> None:
    chunks = split_sections(
        "doc_1",
        "notes.md",
        [ParsedSection("知识库资料", page=None, paragraph=0)],
        chunk_size=100,
        overlap=0,
        knowledge_base_id="kb_team",
    )

    assert chunks[0].chunk_id == "kb_team:doc_1:chunk:00000"
    assert chunks[0].metadata()["knowledge_base_id"] == "kb_team"


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    try:
        split_sections("doc", "a.md", [ParsedSection("abc", None, 0)], 3, 3)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("expected ValueError")
