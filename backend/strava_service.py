"""Strava access: credentials, OAuth tokens, and live route fetching."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("SILENCE_TOKEN_WARNINGS", "true")
from stravalib import Client  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BACKEND_DIR / ".strava_token.json"
# Local fallback only: a credentials.txt with "client_id = ..." / "client_secret = ..." lines.
CREDENTIALS_FILE = Path(os.environ.get("STRAVA_DIR", Path.home() / "Documents" / "strava")) / "credentials.txt"

ROUTE_ID = 3554322571311095478
SCOPES = ["read_all", "profile:read_all", "activity:read_all"]


def _from_secrets() -> tuple[str, str] | None:
    """Read [strava] client_id / client_secret from Streamlit secrets (.streamlit/secrets.toml or Cloud settings)."""
    try:
        import streamlit as st

        sec = st.secrets["strava"]
        return str(sec["client_id"]), str(sec["client_secret"])
    except Exception:
        return None


def _from_file() -> tuple[str, str] | None:
    if not CREDENTIALS_FILE.exists():
        return None
    values = {}
    for line in CREDENTIALS_FILE.read_text().splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            values[key.strip().lower()] = val.strip()
    if "client_id" in values and "client_secret" in values:
        return values["client_id"], values["client_secret"]
    return None


def get_credentials() -> tuple[int, str]:
    """Strava API credentials: Streamlit secrets, then env vars, then the local credentials.txt."""
    creds = _from_secrets()
    if creds is None and os.environ.get("STRAVA_CLIENT_ID") and os.environ.get("STRAVA_CLIENT_SECRET"):
        creds = os.environ["STRAVA_CLIENT_ID"], os.environ["STRAVA_CLIENT_SECRET"]
    if creds is None:
        creds = _from_file()
    if creds is None:
        raise RuntimeError("Strava credentials not found. Add a [strava] section with client_id and "
                           "client_secret to .streamlit/secrets.toml (or the app's Secrets on Streamlit Cloud).")
    return int(creds[0]), creds[1].strip()


# ---------------------------------------------------------------- tokens
def _load_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _save_token(info: dict) -> None:
    TOKEN_FILE.write_text(json.dumps({
        "access_token": info["access_token"],
        "refresh_token": info["refresh_token"],
        "expires_at": int(info["expires_at"]),
    }))
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass


def authorization_url(redirect_uri: str) -> str:
    client_id, _ = get_credentials()
    return Client().authorization_url(client_id=client_id, redirect_uri=redirect_uri, scope=SCOPES)


def exchange_code(code: str) -> None:
    client_id, client_secret = get_credentials()
    info = Client().exchange_code_for_token(client_id=client_id, client_secret=client_secret, code=code.strip())
    _save_token(info)


def has_token() -> bool:
    return _load_token() is not None


def get_client() -> Client | None:
    """Return an authenticated client, refreshing the access token when it is close to expiry."""
    tok = _load_token()
    if tok is None:
        return None
    if tok["expires_at"] - time.time() < 120:
        client_id, client_secret = get_credentials()
        info = Client().refresh_access_token(client_id=client_id, client_secret=client_secret,
                                             refresh_token=tok["refresh_token"])
        _save_token(info)
        tok = _load_token()
    return Client(access_token=tok["access_token"], refresh_token=tok["refresh_token"],
                  token_expires=tok["expires_at"])


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- route
def fetch_route(route_id: int = ROUTE_ID) -> dict:
    """Fetch the route summary and its streams live from Strava (nothing is written to disk).

    Waypoints come straight from the client: client.get_route_streams(route_id)['latlng'].data
    """
    client = get_client()
    if client is None:
        raise RuntimeError("Not connected to Strava.")
    route = client.get_route(route_id)
    streams = client.get_route_streams(route_id)
    latlng = streams["latlng"].data

    def optional(name):
        s = streams.get(name)
        return list(s.data) if s is not None and s.data is not None else None

    if not latlng:
        raise RuntimeError("Route has no latlng stream.")
    return {
        "id": route_id,
        "name": route.name,
        "description": route.description,
        "distance_m": float(route.distance or 0),
        "elevation_gain_m": float(route.elevation_gain or 0),
        "estimated_moving_time_s": int(route.estimated_moving_time or 0),
        "latlng": [[float(a), float(b)] for a, b in latlng],
        "distance": optional("distance"),
        "altitude": optional("altitude"),
        "fetched_at": int(time.time()),
    }

_LOADED_AT = __import__("time").time()  # used by the frontend to hot-reload this module after edits
