"""Shared JSON-file storage for the run plan (start time, pace, stops) and the participant list.

Stored on disk so every browser session sees the same participants and stops.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / ".data"
PLAN_FILE = DATA_DIR / "plan.json"
PEOPLE_FILE = DATA_DIR / "participants.json"
_lock = threading.Lock()

DEFAULT_PLAN = {"start": "07:10", "custom_pace": None, "stops": {}, "notes": {}}


def _backup(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _read(path: Path, default):
    """Read a JSON file. A missing file gives `default`; a damaged one falls back to its last good backup
    and never silently becomes empty (which would let the next save wipe everyone)."""
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


def _write(path: Path, obj) -> None:
    """Durable, atomic write: keep the previous version as .bak, write a temp file, fsync, then swap it in."""
    DATA_DIR.mkdir(exist_ok=True)
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
    dir_fd = os.open(DATA_DIR, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# ------------------------------------------------------------------ plan
def find_participant(name: str) -> dict | None:
    key = name.strip().casefold()
    return next((p for p in load_participants() if p["name"].casefold() == key), None) if key else None


def load_plan() -> dict:
    raw = _read(PLAN_FILE, {})
    plan = {**DEFAULT_PLAN, **raw}
    plan["stops"] = {int(k): float(v) for k, v in plan.get("stops", {}).items() if float(v) > 0}
    plan["notes"] = {int(k): v for k, v in plan.get("notes", {}).items() if v}
    return plan


def save_plan(plan: dict) -> None:
    with _lock:
        _write(PLAN_FILE, {
            "start": plan["start"],
            "custom_pace": plan.get("custom_pace"),
            "stops": {str(k): v for k, v in plan.get("stops", {}).items() if v},
            "notes": {str(k): v for k, v in plan.get("notes", {}).items() if v},
        })


def update_plan(**changes) -> dict:
    plan = load_plan()
    plan.update(changes)
    save_plan(plan)
    return plan


# ------------------------------------------------------------------ participants
def load_participants() -> list[dict]:
    return _read(PEOPLE_FILE, [])


def upsert_participant(name: str, plan: str, join_mi: float | None, leave_mi: float | None) -> bool:
    """Add a participant, or update the one with the same name (case-insensitive). Returns True if updated."""
    name = name.strip()
    with _lock:
        people = load_participants()
        entry = {"name": name, "plan": plan, "join_mi": join_mi, "leave_mi": leave_mi, "updated": int(time.time())}
        for p in people:
            if p["name"].casefold() == name.casefold():
                p.update(entry)
                _write(PEOPLE_FILE, people)
                return True
        people.append({"id": uuid.uuid4().hex, **entry})
        _write(PEOPLE_FILE, people)
        return False


def remove_participants(ids: list[str]) -> None:
    with _lock:
        _write(PEOPLE_FILE, [p for p in load_participants() if p["id"] not in set(ids)])

_LOADED_AT = __import__("time").time()  # used by the frontend to hot-reload this module after edits
