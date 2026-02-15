"""Unit tests for the embedding pipeline — chunking and document loading."""

from pathlib import Path

from src.agent.retrieval.embeddings import (
    _chunk_text,
    _split_by_headings,
    load_runbooks,
)


class TestSplitByHeadings:
    def test_splits_on_h2(self) -> None:
        text = "# Title\n\nIntro\n\n## Section A\n\nContent A\n\n## Section B\n\nContent B"
        sections = _split_by_headings(text, ("\n## ",))
        assert len(sections) == 3
        assert "# Title" in sections[0]
        assert "## Section A" in sections[1]
        assert "## Section B" in sections[2]

    def test_splits_on_h3_within_h2(self) -> None:
        text = "## Overview\n\nIntro\n\n### Part 1\n\nContent 1\n\n### Part 2\n\nContent 2"
        sections = _split_by_headings(text, ("\n## ", "\n### "))
        assert any("### Part 1" in s for s in sections)
        assert any("### Part 2" in s for s in sections)

    def test_no_headings_returns_whole_text(self) -> None:
        text = "Just a paragraph with no headings."
        sections = _split_by_headings(text, ("\n## ",))
        assert len(sections) == 1
        assert sections[0] == text

    def test_empty_text_returns_empty(self) -> None:
        sections = _split_by_headings("", ("\n## ",))
        assert sections == []

    def test_whitespace_only_returns_empty(self) -> None:
        sections = _split_by_headings("   \n\n  ", ("\n## ",))
        assert sections == []


class TestChunkText:
    def test_short_text_single_chunk(self) -> None:
        text = "Short paragraph."
        chunks = _chunk_text(text, chunk_size=800, chunk_overlap=100)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        # Create text with multiple paragraphs that exceed chunk size
        paragraphs = [f"Paragraph {i} with some content to fill space." for i in range(30)]
        text = "\n\n".join(paragraphs)
        chunks = _chunk_text(text, chunk_size=200, chunk_overlap=50)
        assert len(chunks) > 1

    def test_overlap_present(self) -> None:
        paragraphs = [f"Unique paragraph number {i} here." for i in range(20)]
        text = "\n\n".join(paragraphs)
        chunks = _chunk_text(text, chunk_size=150, chunk_overlap=50)
        # With overlap, some content from end of chunk N should appear in chunk N+1
        if len(chunks) >= 2:
            end_of_first = chunks[0][-50:]
            assert end_of_first in chunks[1]


class TestLoadRunbooks:
    def test_loads_from_directory(self, tmp_path: Path) -> None:
        runbook = tmp_path / "test-service.md"
        content = "# Test Service\n\n## Overview\n\nThis is a test.\n\n## Commands\n\n```sh\necho hello\n```\n"
        runbook.write_text(content)
        docs = load_runbooks(tmp_path)
        assert len(docs) > 0
        assert all(d.metadata["source"] == "test-service.md" for d in docs)
        assert all(d.metadata["title"] == "Test Service" for d in docs)

    def test_empty_directory(self, tmp_path: Path) -> None:
        docs = load_runbooks(tmp_path)
        assert docs == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        docs = load_runbooks(tmp_path / "nonexistent")
        assert docs == []

    def test_metadata_includes_section(self, tmp_path: Path) -> None:
        runbook = tmp_path / "dns.md"
        runbook.write_text("# DNS Stack\n\n## Troubleshooting\n\nFix DNS issues here.\n")
        docs = load_runbooks(tmp_path)
        sections = [d.metadata["section"] for d in docs]
        assert any("Troubleshooting" in s for s in sections)

    def test_multiple_runbooks(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("# Service A\n\nContent A\n")
        (tmp_path / "b.md").write_text("# Service B\n\nContent B\n")
        docs = load_runbooks(tmp_path)
        sources = {d.metadata["source"] for d in docs}
        assert "a.md" in sources
        assert "b.md" in sources
