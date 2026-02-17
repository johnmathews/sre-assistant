"""Ingest runbooks into the Chroma vector store.

Usage:
    uv run python -m scripts.ingest_runbooks
    # or via Makefile:
    make ingest
"""

import logging
import shutil
import sys

from src.agent.retrieval.embeddings import CHROMA_PERSIST_DIR, build_vector_store, load_all_documents

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    """Load runbooks, embed them, and persist the vector store."""
    # Clear existing store for a clean rebuild.
    # Delete contents rather than the directory itself — when running in Docker,
    # the directory is a volume mountpoint and cannot be removed (EBUSY).
    if CHROMA_PERSIST_DIR.exists():
        logger.info("Clearing existing vector store at %s", CHROMA_PERSIST_DIR)
        for child in CHROMA_PERSIST_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    documents = load_all_documents()
    if not documents:
        logger.error("No documents found — nothing to ingest")
        sys.exit(1)

    logger.info("Ingesting %d document chunks from all sources...", len(documents))
    store = build_vector_store(documents)

    # Verify
    count = store._collection.count()  # pyright: ignore[reportPrivateUsage]
    logger.info("Done! Vector store contains %d documents", count)


if __name__ == "__main__":
    main()
