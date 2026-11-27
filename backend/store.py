"""Shared storage for the run plan (start time, pace, stops), the participant list, and the Strava token.

Two backends behind the same functions:
- Database (used whenever a URL is configured): set `[database] url = "postgresql://..."` in Streamlit
  secrets, or the DATABASE_URL env var. Data survives restarts/redeploys on Streamlit Cloud.
- Local JSON files in backend/.data/ (fallback for local development).

Each document is stored as one JSON value under a key ("plan", "participants", "strava_token").
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / ".data"
# Where each key lives in the local-file backend (the token keeps its old location).
_LOCAL_PATHS = {"strava_token": BACKEND_DIR / ".strava_token.json"}
_lock = threading.Lock()

DEFAULT_PLAN = {"start": "07:10", "custom_pace": None, "stops": {}, "notes": {}}


# ------------------------------------------------------------------ backend selection
def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        import streamlit as st

        return str(st.secrets["database"]["url"])
    except Exception:
        return None


_engine = None


def _get_engine():
    """SQLAlchemy engine for the configured database, or None to use local files."""
    global _engine
    if _engine is not None:
        return _engine
    url = _database_url()
    if not url:
        return None
    from sqlalchemy import create_engine, text

    # Supabase/Neon/Heroku hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
            break
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=300)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_kv (key VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)"
        ))
    _engine = engine
    return engine


def backend_name() -> str:
    return "database" if _get_engine() is not None else f"local files ({DATA_DIR})"


# ------------------------------------------------------------------ local-file backend
def _path(key: str) -> Path:
    return _LOCAL_PATHS.get(key, DATA_DIR / f"{key}.json")


def _backup(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _file_read(key: str, default):
    """A missing file gives `default`; a damaged one falls back to its last good backup
    and never silently becomes empty (which would let the next save wipe everyone)."""
    path = _path(key)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        bak = _backup(path)
        if bak.exists():
            try:
                return json.loads(bak.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        raise RuntimeError(f"{path} is damaged and has no usable backup; fix or remove it before continuing.")


def _file_write(key: str, obj) -> None:
    """Durable, atomic write: keep the previous version as .bak, write a temp file, fsync, then swap it in."""
    path = _path(key)
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        try:
            json.loads(path.read_text())  # only back up a file that is itself valid
            _backup(path).write_bytes(path.read_bytes())
        except (OSError, json.JSONDecodeError):
            pass
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(obj, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# ------------------------------------------------------------------ generic key/value API
def get_value(key: str, default=None):
    engine = _get_engine()
    if engine is None:
        return _file_read(key, default)
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM app_kv WHERE key = :k"), {"k": key}).first()
    return default if row is None else json.loads(row[0])


def put_value(key: str, obj) -> None:
    engine = _get_engine()
    if engine is None:
        with _lock:
            _file_write(key, obj)
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO app_kv (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ), {"k": key, "v": json.dumps(obj)})


def delete_value(key: str) -> None:
    engine = _get_engine()
    if engine is None:
        _path(key).unlink(missing_ok=True)
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_kv WHERE key = :k"), {"k": key})


def _mutate(key: str, default, fn: Callable[[Any], tuple[Any, Any]]):
    """Read-modify-write one document atomically. `fn(current) -> (new_value, result)`; returns result.

    In the database the row is locked (SELECT ... FOR UPDATE) so two people signing up at once
    can't overwrite each other's entries."""
    engine = _get_engine()
    if engine is None:
        with _lock:
            new, result = fn(_file_read(key, default))
            _file_write(key, new)
        return result
    from sqlalchemy import text

    lock = " FOR UPDATE" if engine.dialect.name == "postgresql" else ""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO app_kv (key, value) VALUES (:k, :v) ON CONFLICT (key) DO NOTHING"
        ), {"k": key, "v": json.dumps(default)})
        row = conn.execute(text(f"SELECT value FROM app_kv WHERE key = :k{lock}"), {"k": key}).first()
        new, result = fn(json.loads(row[0]))
        conn.execute(text("UPDATE app_kv SET value = :v WHERE key = :k"), {"k": key, "v": json.dumps(new)})
    return result


# ------------------------------------------------------------------ plan
def _normalize_plan(raw: dict) -> dict:
    plan = {**DEFAULT_PLAN, **raw}
    plan["stops"] = {int(k): float(v) for k, v in plan.get("stops", {}).items() if float(v) > 0}
    plan["notes"] = {int(k): v for k, v in plan.get("notes", {}).items() if v}
    return plan


def _serialize_plan(plan: dict) -> dict:
    return {
        "start": plan["start"],
        "custom_pace": plan.get("custom_pace"),
        "stops": {str(k): v for k, v in plan.get("stops", {}).items() if v},
        "notes": {str(k): v for k, v in plan.get("notes", {}).items() if v},
    }


def load_plan() -> dict:
    return _normalize_plan(get_value("plan", {}))


def save_plan(plan: dict) -> None:
    put_value("plan", _serialize_plan(plan))


def update_plan(**changes) -> dict:
    def apply(raw):
        plan = _normalize_plan(raw)
        plan.update(changes)
        return _serialize_plan(plan), plan

    return _mutate("plan", {}, apply)


# ------------------------------------------------------------------ participants
def load_participants() -> list[dict]:
    return get_value("participants", [])


def find_participant(name: str) -> dict | None:
    key = name.strip().casefold()
    return next((p for p in load_participants() if p["name"].casefold() == key), None) if key else None


def upsert_participant(name: str, plan: str, join_mi: float | None, leave_mi: float | None) -> bool:
    """Add a participant, or update the one with the same name (case-insensitive). Returns True if updated."""
    name = name.strip()
    entry = {"name": name, "plan": plan, "join_mi": join_mi, "leave_mi": leave_mi, "updated": int(time.time())}

    def apply(people):
        for p in people:
            if p["name"].casefold() == name.casefold():
                p.update(entry)
                return people, True
        people.append({"id": uuid.uuid4().hex, **entry})
        return people, False

    return _mutate("participants", [], apply)


def remove_participants(ids: list[str]) -> None:
    gone = set(ids)
    _mutate("participants", [], lambda people: ([p for p in people if p["id"] not in gone], None))


# ------------------------------------------------------------------ one-time copy of local data to the database
def push_local_to_database() -> None:
    """Copy backend/.data/*.json (and the Strava token) into the configured database."""
    if _get_engine() is None:
        raise SystemExit("No database configured: set [database] url in .streamlit/secrets.toml or DATABASE_URL.")
    for key in ("plan", "participants", "strava_token"):
        value = _file_read(key, None)
        if value is not None:
            put_value(key, value)
            print(f"copied {key}")
    try:
        import tomllib

        strava = tomllib.loads((BACKEND_DIR.parent / ".streamlit" / "secrets.toml").read_text()).get("strava", {})
    except (OSError, ValueError):
        strava = {}
    creds = {k: strava[k] for k in ("client_id", "client_secret", "redirect_uri") if k in strava}
    if creds:
        put_value("strava_credentials", creds)
        print(f"copied strava_credentials ({', '.join(creds)})")


if __name__ == "__main__":
    # Run from the project root:  .venv/bin/python backend/store.py
    push_local_to_database()

_LOADED_AT = __import__("time").time()  # used by the frontend to hot-reload this module after edits
