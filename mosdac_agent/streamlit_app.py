"""Streamlit chat UI for the MOSDAC agent.

Run:
    streamlit run mosdac_agent/streamlit_app.py

Env:
    CHAT_API     — endpoint to POST chat messages (default /mosdac/chat)
    MOSDAC_USER  — value for the X-MOSDAC-User dev header
"""
from __future__ import annotations

import os
import uuid

import requests
import streamlit as st

API = os.getenv("CHAT_API", "http://localhost:8000/mosdac/chat")
USER = os.getenv("MOSDAC_USER", "dev-user")

st.set_page_config(page_title="MOSDAC-Bot", page_icon="🛰️")
st.title("🛰️ MOSDAC Order Assistant")
st.caption(f"Backend: {API} · User: {USER}")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []

for role, msg in st.session_state.history:
    with st.chat_message(role):
        st.markdown(msg)

prompt = st.chat_input(
    "e.g. Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP"
)
if prompt:
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                r = requests.post(
                    API,
                    json={
                        "message": prompt,
                        "session_id": st.session_state.session_id,
                    },
                    headers={"X-MOSDAC-User": USER},
                    timeout=180,
                )
                r.raise_for_status()
                reply = r.json().get("answer", "(no answer)")
            except Exception as exc:
                reply = f"(error: {exc})"
        st.markdown(reply)
        st.session_state.history.append(("assistant", reply))

if st.button("Reset conversation"):
    st.session_state.history.clear()
    st.session_state.session_id = str(uuid.uuid4())
    st.rerun()
