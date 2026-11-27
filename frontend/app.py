"""Group Run Planner — Streamlit entry point.

Run either way from the project root:
    .venv/bin/streamlit run frontend/app.py
    .venv/bin/python frontend/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from streamlit import runtime

# Launched with plain `python app.py` (e.g. PyCharm's Run button)? Re-launch through Streamlit's server.
if __name__ == "__main__" and not runtime.exists():
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()), *sys.argv[1:]]
    sys.exit(stcli.main())

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

common.reload_changed_backend_modules()

st.set_page_config(page_title="Run Sign Up Sheet", page_icon="🏃", layout="wide")

pages = st.navigation([
    st.Page("views/route_plan.py", title="Route plan", icon="🗺️", default=True),
    st.Page("views/participants.py", title="Participants", icon="👥"),
])

ctx = common.sidebar_and_context()
if ctx is None:
    st.title("Run Sign Up Sheet")
    st.info(f"Connect to Strava in the sidebar to load route")
    st.stop()

st.session_state["ctx"] = ctx
pages.run()
