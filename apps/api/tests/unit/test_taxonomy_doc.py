from pathlib import Path

from intake.core.exceptions import taxonomy_markdown

DOC = Path(__file__).resolve().parents[4] / "docs" / "exception-taxonomy.md"


def test_doc_table_matches_code() -> None:
    assert taxonomy_markdown() in DOC.read_text(), (
        "docs/exception-taxonomy.md is out of date; regenerate the table from taxonomy_markdown()"
    )
