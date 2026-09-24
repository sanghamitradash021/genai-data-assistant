from pathlib import Path

import pytest

from app import documents as d
from app.documents import Section, chunk_sections, split_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "CLAUDEEE.md"


def md_chunks(text):
    return chunk_sections([Section(text)], ".md")


def test_headings_become_sections_with_path_and_metadata():
    text = "# Guide\nintro\n\n## 1. Database\n- ORM: Prisma 7\n\n### Migrations\nrun db:migrate\n"
    chunks = md_chunks(text)
    by = {c.heading: c for c in chunks}
    assert by["Guide"].section == "Guide"
    assert by["Database"].section == "Database"  # numbering stripped
    assert "Prisma 7" in by["Database"].text and "Database" not in by["Database"].text.splitlines()[0]
    assert by["Migrations"].section == "Database" and by["Migrations"].heading == "Migrations"
    assert by["Migrations"].heading_path == "Guide > Database > Migrations"


def test_hash_lines_inside_code_fences_are_not_headings():
    text = "## Commands\n```bash\n# Development\npnpm dev\n```\n\n## Next\nx\n"
    secs = split_markdown(text)
    assert [s.heading for s in secs] == ["Commands", "Next"]
    assert "# Development" in secs[0].body


def test_container_headings_without_text_yield_no_chunk_but_keep_path():
    chunks = md_chunks("## API\n### Axios\nuse instance\n")
    assert len(chunks) == 1 and chunks[0].heading_path == "API > Axios" and chunks[0].section == "API"


def test_table_of_contents_is_not_indexed():
    chunks = md_chunks("# T\nintro\n\n## Table of Contents\n- [A](#a)\n- [B](#b)\n\n## A\nreal content\n")
    assert all("[A](#a)" not in c.text for c in chunks) and {c.heading for c in chunks} == {"T", "A"}


def test_large_section_is_split_but_keeps_its_heading_metadata():
    body = "\n".join(f"- rule number {i} says always do the thing carefully" for i in range(80))
    chunks = md_chunks(f"# Doc\n## Rules\n{body}\n")
    assert len(chunks) > 1
    assert all(c.section == "Rules" and c.heading == "Rules" and c.heading_path == "Doc > Rules" for c in chunks)
    assert all(len(c.text) <= d.settings.chunk_size + 50 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_text_before_first_heading_is_kept_without_section():
    chunks = md_chunks("preamble text\n\n# H\nbody\n")
    assert chunks[0].text == "preamble text" and chunks[0].section is None and chunks[0].heading_path is None


def test_embedding_text_anchors_body_to_its_location():
    c = md_chunks("# Doc\n## Database\nORM: Prisma 7\n")[-1]
    assert c.embedding_text("X.md").startswith("X.md > Doc > Database\n") and "Prisma 7" in c.embedding_text("X.md")


def test_non_markdown_formats_are_unchanged_and_have_no_section_metadata():
    chunks = chunk_sections([Section("word " * 600, page=2)], ".txt")
    assert len(chunks) > 1 and all(c.page == 2 and c.section is None and c.heading is None for c in chunks)
    assert chunks[0].embedding_text("f.txt") == "f.txt\n" + chunks[0].text


def test_chunker_version_is_exposed_for_reindexing():
    assert isinstance(d.CHUNKER_VERSION, int) and d.CHUNKER_VERSION >= 2


# ---- the real CLAUDEEE.md -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def claude_chunks():
    return chunk_sections(d.load_sections(FIXTURE), ".md")


@pytest.mark.parametrize("section", [
    "Frontend Standards", "Project Structure", "File Naming", "TypeScript", "Components", "Pages", "Hooks",
    "State Management", "API & Services", "Routing", "Styling", "Testing", "Architecture", "Tech Stack",
    "Database", "Business Rules", "Docker Services", "Rules",
])
def test_claudeee_sections_are_all_indexed(claude_chunks, section):
    assert section in {c.section for c in claude_chunks}


def test_claudeee_database_chunk_holds_the_orm_line(claude_chunks):
    db = [c for c in claude_chunks if c.section == "Database"]
    assert db and any("Prisma 7" in c.text for c in db)


def test_claudeee_shell_comments_did_not_become_sections(claude_chunks):
    assert "Development" not in {c.section for c in claude_chunks}
    assert "Table of Contents" not in {c.section for c in claude_chunks}
