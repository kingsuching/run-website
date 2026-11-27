"""Shared pieces for both pages: Strava sidebar, run settings, schedule context, and the route map."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

import folium
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import schedule as sch  # noqa: E402
import store  # noqa: E402
import strava_service as svc  # noqa: E402


def reload_changed_backend_modules() -> None:
    """Streamlit only hot-reloads modules beside the app; pick up edits to backend/ files without a restart."""
    import importlib

    for mod in (sch, store, svc):
        if Path(mod.__file__).stat().st_mtime > getattr(mod, "_LOADED_AT", 0):
            importlib.reload(mod)


def _redirect_uri() -> str:
    """Where Strava sends the user back after sign-in.

    Secrets win (the URL differs per deployment), then the database's strava_credentials row, then localhost."""
    try:
        return str(st.secrets["strava"]["redirect_uri"])
    except Exception:
        return str(svc.database_config().get("redirect_uri") or "http://localhost:8501")


REDIRECT_URI = _redirect_uri()
ROUTE_TTL_S = 600

PLAN_LABELS = {
    "full": "Full Length",
    "part": "Partial Length",
    "finish": "Meet at Finish - BROOKFIELD PLACE (WTC)",
}

ORANGE = "#fc5200"
BLUE = "#2563eb"


def fmt_t(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def gmaps(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"


@dataclass
class Ctx:
    route: dict
    plan: dict
    speed: float
    start: datetime
    wps: pd.DataFrame
    sched: pd.DataFrame
    cum_mi: list[float]
    total_mi: float
    moving_s: float
    stop_s: float

    @property
    def finish(self) -> pd.Series:
        return self.sched.iloc[-1]

    def time_at(self, mile: float, kind: str = "arrive") -> datetime:
        return sch.time_at_mile(self.sched, mile, self.speed, self.start, kind)

    def point_at(self, mile: float) -> list[float]:
        return self.route["latlng"][sch.nearest_point_index(self.route, mile)]

    def participant_window(self, p: dict) -> tuple[float, float] | None:
        if p["plan"] == "full":
            return 0.0, self.total_mi
        if p["plan"] == "part":
            return p["join_mi"], p["leave_mi"]
        return None


# ------------------------------------------------------------------ Strava
@st.cache_data(ttl=ROUTE_TTL_S, show_spinner="Loading route from Strava…")
def load_route(route_id: int) -> dict:
    return svc.fetch_route(route_id)


def _handle_oauth_callback() -> None:
    code = st.query_params.get("code")
    if code:
        try:
            svc.exchange_code(code)
            load_route.clear()
            st.toast("Connected to Strava.")
        except Exception as e:
            st.error(f"Strava sign-in failed: {e}")
        st.query_params.clear()


def _connect_ui() -> None:
    st.markdown("Connect your Strava account so the app can load the route.")
    try:
        url = svc.authorization_url(REDIRECT_URI)
    except Exception as e:
        st.error(f"Could not read Strava credentials: {e}")
        return
    st.link_button("Connect with Strava", url, type="primary")
    with st.expander("Redirect didn't work? Paste the code"):
        code = st.text_input("Authorization code (the `code=` value in the address bar)")
        if st.button("Submit code") and code:
            try:
                svc.exchange_code(code)
                load_route.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Strava sign-in failed: {e}")


# ------------------------------------------------------------------ settings callbacks (shared, saved to disk)
def _save_start():
    store.update_plan(start=st.session_state["start_time"].strftime("%H:%M"))


def _save_pace():
    ss = st.session_state
    if ss["pace_mode"] == "Custom pace":
        # Switching to custom fires before the min/sec boxes exist: start from the current default pace.
        if "pace_min" in ss and "pace_sec" in ss:
            pace = ss["pace_min"] + ss["pace_sec"] / 60
        else:
            pace = ss.get("_pace_default", 10.0)
        store.update_plan(custom_pace=pace)
    else:
        store.update_plan(custom_pace=None)


def sidebar_and_context() -> Ctx | None:
    """Render the shared sidebar and build the schedule context. Returns None until a route is available."""
    _handle_oauth_callback()
    route = None
    with st.sidebar:
        st.header("Strava")
        if svc.has_token():
            try:
                route = load_route(svc.ROUTE_ID)
            except Exception as e:
                st.error(f"Could not load the route: {e}")
            c1, c2 = st.columns(2)
            if c1.button("Refresh route", width="stretch"):
                load_route.clear()
                st.rerun()
            if c2.button("Disconnect", width="stretch"):
                svc.disconnect()
                load_route.clear()
                st.rerun()
            if route:
                st.caption(f"As of {datetime.fromtimestamp(route['fetched_at']):%a, %b %d, %Y at %-I:%M %p}")
        else:
            _connect_ui()

    if route is None:
        return None

    plan = store.load_plan()
    route_speed = sch.average_speed_mps(route)
    with st.sidebar:
        st.header("Run Settings")
        st.time_input("Start time", value=datetime.strptime(plan["start"], "%H:%M").time(), step=60,
                      key="start_time", on_change=_save_start)
        opts = ["Route average (Strava estimate)", "Custom pace"] if route_speed else ["Custom pace"]
        custom = plan.get("custom_pace")
        st.session_state["_pace_default"] = sch.pace_min_per_mile(route_speed) if route_speed else 10.0
        st.radio("Pace", opts, index=opts.index("Custom pace") if custom or not route_speed else 0,
                 key="pace_mode", on_change=_save_pace)
        if st.session_state["pace_mode"] == "Custom pace":
            default = store.load_plan().get("custom_pace") or st.session_state["_pace_default"]
            d_min, d_sec = divmod(round(default * 60), 60)
            p1, p2 = st.columns(2)
            p1.number_input("min / mi", 4, 30, min(max(d_min, 4), 30), key="pace_min", on_change=_save_pace)
            p2.number_input("sec", 0, 59, d_sec, key="pace_sec", on_change=_save_pace)
            speed = sch.speed_from_pace(st.session_state["pace_min"] + st.session_state["pace_sec"] / 60)
        else:
            speed = route_speed

    start_t = st.session_state.get("start_time") or time(7, 10)
    start = datetime.combine(date.today(), start_t)
    wps = sch.waypoints(route)
    sched = sch.build_schedule(wps, speed, start, plan["stops"])
    return Ctx(route=route, plan=plan, speed=speed, start=start, wps=wps, sched=sched,
               cum_mi=sch.cumulative_miles(route), total_mi=route["distance_m"] / sch.M_PER_MILE,
               moving_s=route["distance_m"] / speed, stop_s=sched["stop_min"].sum() * 60)


# ------------------------------------------------------------------ map
def _weight(count: int) -> float:
    return 3 if count == 0 else min(3 + 3 * count, 24)


def build_map(ctx: Ctx, participants: list[dict], draft: tuple[float, float] | None = None,
              highlight_finish: bool = False) -> folium.Map:
    """Route map: line thickness = number of people on each stretch; dots = every 10th route point."""
    latlng = ctx.route["latlng"]
    lats, lons = [p[0] for p in latlng], [p[1] for p in latlng]
    m = folium.Map(location=[sum(lats) / len(lats), sum(lons) / len(lons)], tiles="OpenStreetMap",
                   control_scale=True)
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(20, 20))

    # Route stretches, thickness by head-count
    for seg in sch.segment_coverage(ctx.wps, participants, ctx.total_mi):
        n = seg["count"]
        who = ", ".join(seg["names"][:12]) + (" …" if n > 12 else "")
        tip = f"<b>Mile {seg['m0']:.2f}–{seg['m1']:.2f}</b><br>{n} runner{'s' if n != 1 else ''}" + \
              (f": {who}" if n else "")
        folium.PolyLine(latlng[seg["i0"]:seg["i1"] + 1], color=ORANGE, weight=_weight(n),
                        opacity=0.9 if n else 0.55, tooltip=tip, line_cap="round").add_to(m)

    # The current user's draft segment
    if draft:
        i0, i1 = sch.nearest_point_index(ctx.route, draft[0]), sch.nearest_point_index(ctx.route, draft[1])
        folium.PolyLine(latlng[i0:i1 + 1], color=BLUE, weight=4, dash_array="8 8",
                        tooltip="Your segment").add_to(m)
        for mile, idx, label in ((draft[0], i0, "Merge"), (draft[1], i1, "Depart")):
            kind = "arrive"
            folium.Marker(latlng[idx], tooltip=f"<b>{label}</b> · mile {mile:.2f} · {fmt_t(ctx.time_at(mile, kind))}",
                          icon=folium.Icon(color="blue", icon="play" if label == "Merge" else "stop")).add_to(m)

    # Waypoint dots with ETAs
    for _, r in ctx.sched.iloc[1:-1].iterrows():
        stop = r["stop_min"]
        tip = f"<b>Waypoint {r['wp']}</b><br>Mile {r['mile']:.2f} · arrive {fmt_t(r['arrive'])}"
        if stop:
            tip += f"<br>{stop:g} min stop → leave {fmt_t(r['depart'])}"
        folium.CircleMarker([r["lat"], r["lon"]], radius=6 if stop else 2.5, color="#dc2626" if stop else "#555",
                            weight=1, fill=True, fill_color="#dc2626" if stop else "#fff", fill_opacity=1,
                            tooltip=tip).add_to(m)

    # Start / finish (combined when the route is a loop)
    s, f = ctx.sched.iloc[0], ctx.finish
    meeting = [p["name"] for p in participants if p["plan"] == "finish"]
    fin_tip = f"<b>Finish</b> · mile {f['mile']:.2f} · {fmt_t(f['arrive'])}"
    if meeting:
        fin_tip += f"<br>Meeting here: {', '.join(meeting)}"
    start_tip = f"<b>Start</b> · {fmt_t(s['arrive'])}"
    if sch.haversine_m((s["lat"], s["lon"]), (f["lat"], f["lon"])) < 150:
        folium.Marker([s["lat"], s["lon"]], tooltip=f"{start_tip}<br>{fin_tip}",
                      icon=folium.Icon(color="red" if highlight_finish else "green", icon="flag")).add_to(m)
    else:
        folium.Marker([s["lat"], s["lon"]], tooltip=start_tip, icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker([f["lat"], f["lon"]], tooltip=fin_tip,
                      icon=folium.Icon(color="red" if highlight_finish else "black", icon="flag")).add_to(m)
    return m
