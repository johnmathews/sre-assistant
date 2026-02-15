"""Embedding pipeline for loading, chunking, and storing runbook documents."""

import logging
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from src.config import get_settings

logger = logging.getLogger(__name__)

RUNBOOKS_DIR = Path(__file__).resolve().parents[3] / "runbooks"
CHROMA_PERSIST_DIR = Path(__file__).resolve().parents[3] / ".chroma_db"
COLLECTION_NAME = "runbooks"

# Chunking parameters
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
HEADING_SEPARATORS = ("\n## ", "\n### ")


def _split_by_headings(text: str, separators: tuple[str, ...]) -> list[str]:
    """Split markdown text on heading boundaries, keeping the heading with its content."""
    chunks: list[str] = []
    current = text

    for sep in separators:
        parts = current.split(sep)
        if len(parts) > 1:
            # First part is content before first heading at this level
            result: list[str] = [parts[0]]
            for part in parts[1:]:
                result.append(sep.lstrip("\n") + part)
            # Recursively split each part by remaining separators
            remaining_seps = tuple(s for s in separators if s != sep)
            for part in result:
                if remaining_seps and len(part) > CHUNK_SIZE:
                    chunks.extend(_split_by_headings(part, remaining_seps))
                else:
                    chunks.append(part)
            return [c for c in chunks if c.strip()]

    return [current] if current.strip() else []


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text into overlapping chunks, respecting paragraph boundaries."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > chunk_size and current_chunk:
            chunks.append(current_chunk.strip())
            # Keep overlap from end of previous chunk
            overlap_text = current_chunk[-chunk_overlap:] if len(current_chunk) > chunk_overlap else current_chunk
            current_chunk = overlap_text + "\n\n" + para
        else:
            current_chunk = current_chunk + "\n\n" + para if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def load_runbooks(runbooks_dir: Path | None = None) -> list[Document]:
    """Load and chunk all markdown runbooks into LangChain Documents.

    Each document gets metadata with the source filename and section heading.
    """
    directory = runbooks_dir or RUNBOOKS_DIR
    documents: list[Document] = []

    if not directory.exists():
        logger.warning("Runbooks directory not found: %s", directory)
        return documents

    md_files = sorted(directory.glob("*.md"))
    if not md_files:
        logger.warning("No markdown files found in %s", directory)
        return documents

    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        runbook_name = md_file.stem

        # Extract title from first H1
        title = runbook_name
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # Split by headings first, then chunk large sections
        sections = _split_by_headings(text, HEADING_SEPARATORS)

        for section in sections:
            sub_chunks = _chunk_text(section, CHUNK_SIZE, CHUNK_OVERLAP) if len(section) > CHUNK_SIZE else [section]

            for i, chunk in enumerate(sub_chunks):
                # Extract section heading from chunk
                section_heading = ""
                for line in chunk.splitlines():
                    if line.startswith("#"):
                        section_heading = line.lstrip("#").strip()
                        break

                documents.append(Document(
                    page_content=chunk,
                    metadata={
                        "source": md_file.name,
                        "runbook": runbook_name,
                        "title": title,
                        "section": section_heading,
                        "chunk_index": i,
                    },
                ))

    logger.info("Loaded %d chunks from %d runbook files", len(documents), len(md_files))
    return documents


def get_embeddings() -> OpenAIEmbeddings:
    """Create an OpenAI embeddings instance using project settings."""
    settings = get_settings()
    return OpenAIEmbeddings(
        api_key=settings.openai_api_key,  # type: ignore[arg-type]
        model="text-embedding-3-small",
    )


def build_vector_store(
    documents: list[Document] | None = None,
    persist_directory: Path | None = None,
) -> Chroma:
    """Build (or rebuild) the Chroma vector store from runbook documents.

    If documents is None, loads from the default runbooks directory.
    """
    docs = documents if documents is not None else load_runbooks()
    persist_dir = str(persist_directory or CHROMA_PERSIST_DIR)

    if not docs:
        logger.warning("No documents to embed — vector store will be empty")

    embeddings = get_embeddings()

    logger.info("Building vector store with %d documents at %s", len(docs), persist_dir)
    vector_store = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        persist_directory=persist_dir,
    )
    logger.info("Vector store built successfully")
    return vector_store


def load_vector_store(persist_directory: Path | None = None) -> Chroma:
    """Load an existing Chroma vector store from disk."""
    persist_dir = str(persist_directory or CHROMA_PERSIST_DIR)
    embeddings = get_embeddings()

    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=persist_dir,
    )
