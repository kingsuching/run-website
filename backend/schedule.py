"""Pure schedule math: waypoints, ETAs with stops, and mile-marker arrival/departure times."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

M_PER_MILE = 1609.344
FT_PER_M = 3.28084
WAYPOINT_STEP = 10


def haversine_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def cumulative_distance_m(route: dict) -> list[float]:
    """Distance along the route at each latlng point, scaled so the end matches the route's total distance."""
    latlng = route["latlng"]
    dist = route.get("distance")
    if not dist or len(dist) != len(latlng):
        dist = [0.0]
        for p, q in zip(latlng, latlng[1:]):
            dist.append(dist[-1] + haversine_m(p, q))
    total = route.get("distance_m") or dist[-1]
    scale = total / dist[-1] if dist[-1] else 1.0
    return [d * scale for d in dist]


def average_speed_mps(route: dict) -> float | None:
    t = route.get("estimated_moving_time_s") or 0
    d = route.get("distance_m") or 0
    return d / t if t > 0 and d > 0 else None


def pace_min_per_mile(speed_mps: float) -> float:
    return M_PER_MILE / speed_mps / 60


def speed_from_pace(pace_min_mile: float) -> float:
    return M_PER_MILE / (pace_min_mile * 60)


def fmt_pace(pace_min_mile: float) -> str:
    m, s = divmod(round(pace_min_mile * 60), 60)
    return f"{m}:{s:02d} /mi"


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def waypoints(route: dict, step: int = WAYPOINT_STEP) -> pd.DataFrame:
    """Every `step`-th point of the route stream, always including the finish."""
    latlng = route["latlng"]
    dist = cumulative_distance_m(route)
    alt = route.get("altitude")
    idx = list(range(0, len(latlng), step))
    if idx[-1] != len(latlng) - 1:
        idx.append(len(latlng) - 1)
    rows = []
    for n, i in enumerate(idx):
        rows.append({
            "wp": n,
            "point_index": i,
            "lat": latlng[i][0],
            "lon": latlng[i][1],
            "mile": dist[i] / M_PER_MILE,
            "elev_ft": alt[i] * FT_PER_M if alt and i < len(alt) else None,
        })
    df = pd.DataFrame(rows)
    df["label"] = [""] * len(df)
    df.loc[0, "label"] = "Start"
    df.loc[len(df) - 1, "label"] = "Finish"
    return df


def build_schedule(wps: pd.DataFrame, speed_mps: float, start: datetime, stops_min: dict[int, float]) -> pd.DataFrame:
    """Arrival/departure at each waypoint. A stop at waypoint n delays every later waypoint."""
    df = wps.copy()
    arrive, depart, stop_col = [], [], []
    stop_total = 0.0
    for _, r in df.iterrows():
        moving_s = r["mile"] * M_PER_MILE / speed_mps
        a = start + timedelta(seconds=moving_s + stop_total)
        stop = float(stops_min.get(int(r["wp"]), 0) or 0)
        arrive.append(a)
        depart.append(a + timedelta(minutes=stop))
        stop_col.append(stop)
        stop_total += stop * 60
    df["stop_min"] = stop_col
    df["arrive"] = arrive
    df["depart"] = depart
    return df


def time_at_mile(sched: pd.DataFrame, mile: float, speed_mps: float, start: datetime, kind: str = "arrive") -> datetime:
    """Time a runner reaches (kind='arrive') or leaves (kind='depart') a given mile marker.

    Stops at waypoints strictly before the marker always count; a stop exactly at the marker
    counts only for departure.
    """
    eps = 1e-6
    before = sched[sched["mile"] < mile - eps]["stop_min"].sum()
    at = sched[(sched["mile"] - mile).abs() <= eps]["stop_min"].sum()
    stops = before + (at if kind == "depart" else 0)
    return start + timedelta(seconds=mile * M_PER_MILE / speed_mps + stops * 60)


def nearest_point_index(route: dict, mile: float) -> int:
    dist = cumulative_distance_m(route)
    target = mile * M_PER_MILE
    return min(range(len(dist)), key=lambda i: abs(dist[i] - target))


def cumulative_miles(route: dict) -> list[float]:
    return [d / M_PER_MILE for d in cumulative_distance_m(route)]


def snap_to_route(route: dict, lat: float, lon: float, min_index: int = 0) -> tuple[int, float]:
    """Nearest route point (index >= min_index) to a clicked location. Returns (index, distance in metres)."""
    latlng = route["latlng"]
    best_i, best_d = min_index, float("inf")
    for i in range(min_index, len(latlng)):
        d = haversine_m(latlng[i], (lat, lon))
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


def segment_coverage(wps: pd.DataFrame, participants: list[dict], total_mi: float) -> list[dict]:
    """For each stretch between consecutive waypoints, who is on the route there."""
    eps = 1e-6
    rows = wps.reset_index(drop=True)
    out = []
    for k in range(len(rows) - 1):
        m0, m1 = rows.loc[k, "mile"], rows.loc[k + 1, "mile"]
        names = []
        for p in participants:
            if p["plan"] == "full":
                j, l = 0.0, total_mi
            elif p["plan"] == "part":
                j, l = p["join_mi"], p["leave_mi"]
            else:
                continue
            if j < m1 - eps and l > m0 + eps:
                names.append(p["name"])
        out.append({"k": k, "i0": int(rows.loc[k, "point_index"]), "i1": int(rows.loc[k + 1, "point_index"]),
                    "m0": m0, "m1": m1, "names": names, "count": len(names)})
    return out

_LOADED_AT = __import__("time").time()  # used by the frontend to hot-reload this module after edits
