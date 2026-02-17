"""Streamlit chat UI for the SRE Assistant.

Talks to the FastAPI backend via httpx. Run with: make ui
"""

import os
from uuid import uuid4

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="SRE Assistant", layout="centered")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid4().hex[:8]

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("SRE Assistant")

    if st.button("New conversation"):
        st.session_state.messages = []
        st.session_state.session_id = uuid4().hex[:8]
        st.rerun()

    st.divider()

    st.caption(f"Session: `{st.session_state.session_id}`")

    # Health check
    st.subheader("Infrastructure Health")
    try:
        health_resp = httpx.get(f"{API_URL}/health", timeout=5.0)
        health_data: dict[str, object] = health_resp.json()
        overall = health_data.get("status", "unknown")
        model_name = health_data.get("model")
        if isinstance(model_name, str) and model_name:
            st.caption(f"LLM: `{model_name}`")

        if overall == "healthy":
            st.success(f"Overall: {overall}")
        elif overall == "degraded":
            st.warning(f"Overall: {overall}")
        else:
            st.error(f"Overall: {overall}")

        components = health_data.get("components", [])
        if isinstance(components, list):
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                name = comp.get("name", "unknown")
                status = comp.get("status", "unknown")
                detail = comp.get("detail")
                icon = ":white_check_mark:" if status == "healthy" else ":x:"
                label = f"{icon} {name}: {status}"
                if detail:
                    label += f" — {detail}"
                st.markdown(label)
    except httpx.ConnectError:
        st.error("Cannot reach API server. Is `make serve` running?")
    except Exception as exc:
        st.error(f"Health check failed: {exc}")

# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------

if prompt := st.chat_input("Ask about your infrastructure..."):
    # Display user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call the API
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                resp = httpx.post(
                    f"{API_URL}/ask",
                    json={"question": prompt, "session_id": st.session_state.session_id},
                    timeout=120.0,
                )
                resp.raise_for_status()
                data: dict[str, str] = resp.json()
                answer = data.get("response", "No response received.")
                # Update session_id in case the server generated one
                returned_sid = data.get("session_id")
                if returned_sid:
                    st.session_state.session_id = returned_sid
            except httpx.ConnectError:
                answer = "Cannot reach the API server. Make sure `make serve` is running."
            except httpx.HTTPStatusError as exc:
                answer = f"API error (HTTP {exc.response.status_code}): {exc.response.text}"
            except Exception as exc:
                answer = f"Unexpected error: {exc}"

        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
