"""Ingest runbooks into the Chroma vector store.

Usage:
    uv run python -m scripts.ingest_runbooks
    # or via Makefile:
    make ingest
"""

import logging
import shutil
import sys

from src.agent.retrieval.embeddings import CHROMA_PERSIST_DIR, build_vector_store, load_runbooks

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    """Load runbooks, embed them, and persist the vector store."""
    # Clear existing store for a clean rebuild
    if CHROMA_PERSIST_DIR.exists():
        logger.info("Removing existing vector store at %s", CHROMA_PERSIST_DIR)
        shutil.rmtree(CHROMA_PERSIST_DIR)

    documents = load_runbooks()
    if not documents:
        logger.error("No runbook documents found — nothing to ingest")
        sys.exit(1)

    logger.info("Ingesting %d document chunks...", len(documents))
    store = build_vector_store(documents)

    # Verify
    count = store._collection.count()  # pyright: ignore[reportPrivateUsage]
    logger.info("Done! Vector store contains %d documents", count)


if __name__ == "__main__":
    main()
