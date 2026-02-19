"""Persist conversation history to JSON files for debugging and analysis."""

import contextlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import BaseMessage, messages_to_dict

logger = logging.getLogger(__name__)


def save_conversation(
    history_dir: str,
    session_id: str,
    messages: list[Any],
    model: str,
) -> None:
    """Save conversation messages to a JSON file. Never raises.

    Args:
        history_dir: Directory to write JSON files into.
        session_id: Conversation session ID (used as filename).
        messages: List of LangChain message objects from the agent result.
        model: The LLM model name used for this conversation.
    """
    try:
        _save_conversation_inner(history_dir, session_id, messages, model)
    except Exception:
        logger.exception("Failed to save conversation history for session '%s'", session_id)


def _save_conversation_inner(
    history_dir: str,
    session_id: str,
    messages: list[Any],
    model: str,
) -> None:
    """Inner implementation that may raise on I/O or serialization errors."""
    # Filter to only BaseMessage instances (skip any non-message items)
    valid_messages: list[BaseMessage] = [m for m in messages if isinstance(m, BaseMessage)]
    if not valid_messages:
        logger.debug("No messages to save for session '%s'", session_id)
        return

    serialized = messages_to_dict(valid_messages)
    now = datetime.now(UTC).isoformat()

    # Preserve created_at from existing file if present
    filepath = os.path.join(history_dir, f"{session_id}.json")
    created_at = now
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                existing: dict[str, Any] = json.load(f)
            created_at = existing.get("created_at", now)
        except (json.JSONDecodeError, OSError):
            pass  # Corrupted file — overwrite with new created_at

    payload: dict[str, Any] = {
        "session_id": session_id,
        "created_at": created_at,
        "updated_at": now,
        "turn_count": sum(1 for m in serialized if m.get("type") == "human"),
        "model": model,
        "messages": serialized,
    }

    os.makedirs(history_dir, exist_ok=True)

    # Atomic write: write to temp file then rename
    fd, tmp_path = tempfile.mkstemp(dir=history_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp_path, filepath)
    except BaseException:
        # Clean up temp file on any error
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
