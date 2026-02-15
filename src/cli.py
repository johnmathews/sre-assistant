"""Simple CLI REPL for the SRE assistant agent.

Usage:
    uv run python -m src.cli
    # or via Makefile:
    make chat
"""

import logging
import sys
import uuid

from src.agent.agent import build_agent, invoke_agent

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)


def main() -> None:
    """Run the interactive CLI loop."""
    print("SRE Assistant (type 'quit' or Ctrl+C to exit)")
    print("=" * 50)

    try:
        agent = build_agent()
    except Exception as e:
        print(f"Failed to build agent: {e}")
        print("Check your .env file has valid OPENAI_API_KEY.")
        sys.exit(1)

    session_id = uuid.uuid4().hex[:8]
    print(f"Session: {session_id}\n")

    while True:
        try:
            question = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        try:
            response = invoke_agent(agent, question, session_id=session_id)
            print(f"\nAssistant: {response}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
