"""Route plan page: details, the join form (typed or clicked mile markers), map, and ETAs with stops."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import common as C
from common import sch, store

ss = st.session_state
ctx: C.Ctx = ss["ctx"]
route, sched, total = ctx.route, ctx.sched, ctx.total_mi
max_mi = round(total, 2)
participants = store.load_participants()

PICK_TYPE, PICK_MAP = "Type mile markers", "Click two points on the map"


def _prefill_from(p: dict) -> None:
    """Load a saved participant's plan into the form widgets."""
    ss["f_name"] = p["name"]
    ss["f_plan"] = p["plan"]
    if p["plan"] == "part" and p.get("join_mi") is not None:
        ss["join_mi"], ss["leave_mi"] = float(p["join_mi"]), float(p["leave_mi"])


def _on_name_change() -> None:
    p = store.find_participant(ss.get("f_name", ""))
    if p:
        _prefill_from(p)
        st.query_params["me"] = p["name"]


# After a reload (or reopening a saved link) the URL's ?me=<name> restores this person's saved plan.
if not ss.get("_restored"):
    ss["_restored"] = True
    me = st.query_params.get("me")
    saved = store.find_participant(me) if me else None
    if saved:
        _prefill_from(saved)

# Apply values produced by map clicks on the previous run (before the widgets exist).
for k in ("join_mi", "leave_mi"):
    if f"pending_{k}" in ss:
        ss[k] = ss.pop(f"pending_{k}")
ss.setdefault("join_mi", 0.0)
ss.setdefault("leave_mi", max_mi)
ss.setdefault("click_step", 0)  # 0 -> next click sets merge point, 1 -> depart point
ss["join_mi"] = min(max(ss["join_mi"], 0.0), max_mi)
ss["leave_mi"] = min(max(ss["leave_mi"], 0.0), max_mi)

# ------------------------------------------------------------------ header + route details
st.title(f"🏃 {route['name']}")
if route.get("description"):
    st.caption(route["description"])

m = st.columns(6)
m[0].metric("Distance", f"{total:.2f} mi")
m[1].metric("Elevation gain", f"{route['elevation_gain_m'] * sch.FT_PER_M:,.0f} ft")
m[2].metric("Average speed", f"{ctx.speed / sch.M_PER_MILE * 3600:.2f} mph",
            sch.fmt_pace(sch.pace_min_per_mile(ctx.speed)), delta_color="off")
m[3].metric("Moving time", sch.fmt_duration(ctx.moving_s))
m[4].metric("Total w/ stops", sch.fmt_duration(ctx.moving_s + ctx.stop_s),
            f"+{sch.fmt_duration(ctx.stop_s)} stopped" if ctx.stop_s else None, delta_color="off")
m[5].metric("Finish ETA", C.fmt_t(ctx.finish["arrive"]))

if route.get("altitude"):
    prof = pd.DataFrame({"Mile": ctx.cum_mi, "Elevation (ft)": [a * sch.FT_PER_M for a in route["altitude"]]})
    with st.expander("Elevation profile"):
        st.altair_chart(alt.Chart(prof).mark_area(opacity=0.35, line=True, color=C.ORANGE).encode(
            x="Mile:Q", y=alt.Y("Elevation (ft):Q", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("Mile:Q", format=".2f"), alt.Tooltip("Elevation (ft):Q", format=".0f")],
        ).properties(height=200), width="stretch")

# ------------------------------------------------------------------ join form (centered)
_, mid, _ = st.columns([1, 2.4, 1])
draft = None
with mid, st.container(border=True):
    st.subheader("📝 Join the run", anchor=False)
    name = st.text_input("Your name", key="f_name", placeholder="e.g. Alex", on_change=_on_name_change,
                         help="Already signed up? Type your name to load your saved plan.")
    plan_code = st.radio("How are you taking part?", list(C.PLAN_LABELS), format_func=C.PLAN_LABELS.get,
                         key="f_plan")
    valid = True

    if plan_code == "part":
        pick = st.radio("Set where you merge and depart by", [PICK_TYPE, PICK_MAP], horizontal=True, key="pick_mode")
        if pick == PICK_MAP:
            target = "merge (enter)" if ss.click_step == 0 else "depart (leave)"
            ic, rc = st.columns([4, 1])
            ic.info(f"Click the route on the map below — next click sets your **{target}** point.")
            if rc.button("Reset", width="stretch"):
                ss.click_step = 0
                ss.pending_join_mi, ss.pending_leave_mi = 0.0, max_mi
                st.rerun()
            if ss.get("click_msg"):
                st.warning(ss.pop("click_msg"))
        c1, c2 = st.columns(2)
        join_mi = c1.number_input("Merge at mile", 0.0, max_mi, step=0.1, format="%.2f", key="join_mi")
        leave_mi = c2.number_input("Depart at mile", 0.0, max_mi, step=0.1, format="%.2f", key="leave_mi")
        if leave_mi <= join_mi:
            st.warning("The depart mile must be after the merge mile.")
            valid = False
        else:
            draft = (join_mi, leave_mi)
            t_in, t_out = ctx.time_at(join_mi), ctx.time_at(leave_mi)
            r = st.columns(4)
            r[0].metric("Arrive (merge)", C.fmt_t(t_in))
            r[1].metric("Depart", C.fmt_t(t_out))
            r[2].metric("Distance", f"{leave_mi - join_mi:.2f} mi")
            r[3].metric("With the group", sch.fmt_duration((t_out - t_in).total_seconds()))
            st.markdown(f"Be at the [merge point]({C.gmaps(*ctx.point_at(join_mi))}) by **{C.fmt_t(t_in)}**; "
                        f"the group reaches your [depart point]({C.gmaps(*ctx.point_at(leave_mi))}) "
                        f"around **{C.fmt_t(t_out)}**.")
            stop_here = sched[(sched["mile"] - join_mi).abs() < 1e-6]["stop_min"].sum()
            if stop_here:
                st.caption(f"The group stops {stop_here:g} min at your merge point and leaves at "
                           f"{C.fmt_t(ctx.time_at(join_mi, 'depart'))}.")
    elif plan_code == "finish":
        f = ctx.finish
        r = st.columns(3)
        r[0].metric("Group reaches finish", C.fmt_t(f["arrive"]))
        r[1].metric("After start", sch.fmt_duration((f["arrive"] - ctx.start).total_seconds()))
        r[2].metric("Route distance", f"{total:.2f} mi")
        st.markdown(f"Meet everyone at the [finish]({C.gmaps(f['lat'], f['lon'])}) by **{C.fmt_t(f['arrive'])}**.")
    else:
        st.markdown(f"Start **{C.fmt_t(ctx.start)}** · finish around **{C.fmt_t(ctx.finish['arrive'])}** "
                    f"({total:.2f} mi).")

    existing = next((p for p in participants if p["name"].casefold() == name.strip().casefold()), None)
    label = "Update my plan" if existing else "Add me to the participant list"
    if st.button(label, type="primary", width="stretch", disabled=not (name.strip() and valid)):
        jm, lm = (draft if plan_code == "part" else (None, None))
        updated = store.upsert_participant(name, plan_code, jm, lm)
        st.query_params["me"] = name.strip()  # a reload of this page restores this plan
        st.toast(f"{'Updated' if updated else 'Added'} {name.strip()} — saved.")
        st.rerun()
    if not name.strip():
        st.caption("Enter your name to save your plan. Using an existing name updates that person's plan.")

# ------------------------------------------------------------------ map
st.subheader("Route map")
fmap = C.build_map(ctx, participants, draft=draft, highlight_finish=(plan_code == "finish"))
out = st_folium(fmap, key="route_map", height=560, use_container_width=True, returned_objects=["last_clicked"])
st.caption("Line thickness = number of participants on that stretch · dots are every 10th route point "
           "(red = planned stop) · hover anything for times."
           + (" · dashed blue = your segment" if draft else ""))

click = (out or {}).get("last_clicked")
if click and click != ss.get("last_click_seen"):
    ss.last_click_seen = click  # remember every click so stale ones aren't replayed later
    if plan_code == "part" and ss.get("pick_mode") == PICK_MAP:
        if ss.click_step == 0:
            idx, dist = sch.snap_to_route(route, click["lat"], click["lng"])
        else:
            first_after = next((i for i, mi in enumerate(ctx.cum_mi) if mi > ss["join_mi"] + 1e-6),
                               len(ctx.cum_mi) - 1)
            idx, dist = sch.snap_to_route(route, click["lat"], click["lng"], min_index=first_after)
        if dist > 400:
            ss.click_msg = f"That click was {dist:.0f} m from the route — click closer to the line."
        else:
            mile = round(ctx.cum_mi[idx], 2)
            if ss.click_step == 0:
                ss.pending_join_mi = mile
                ss.pending_leave_mi = max(ss["leave_mi"], min(round(mile + 0.01, 2), max_mi))
                ss.click_step = 1
            else:
                ss.pending_leave_mi = mile
                ss.click_step = 0
        st.rerun()

# ------------------------------------------------------------------ ETAs + stops
st.subheader("Estimated arrival times")
st.caption(f"Every {sch.WAYPOINT_STEP}th route point at {sch.fmt_pace(sch.pace_min_per_mile(ctx.speed))} from "
           f"{C.fmt_t(ctx.start)}. Enter minutes in **Stop (min)** to add a longer stop; all later times shift. "
           "Stops are shared with everyone.")

ss.setdefault("editor_version", 0)
editor_key = f"eta_editor_{ss.editor_version}"


def apply_edits():
    plan = store.load_plan()
    for row, changes in ss[editor_key]["edited_rows"].items():
        wp = int(row)  # row index == waypoint number
        if "Stop (min)" in changes:
            v = changes["Stop (min)"]
            if v:
                plan["stops"][wp] = float(v)
            else:
                plan["stops"].pop(wp, None)
        if "Note" in changes:
            plan["notes"][wp] = changes["Note"] or ""
    store.save_plan(plan)
    ss.editor_version += 1


table = pd.DataFrame({
    "WP": sched["wp"],
    "Point": sched["label"],
    "Mile": sched["mile"].round(2),
    "Elev (ft)": sched["elev_ft"].round(0),
    "Arrive": sched["arrive"].map(C.fmt_t),
    "Stop (min)": sched["stop_min"].astype(float),
    "Depart": sched["depart"].map(C.fmt_t),
    "Note": [ctx.plan["notes"].get(int(w), "") for w in sched["wp"]],
})
if table["Elev (ft)"].isna().all():
    table = table.drop(columns="Elev (ft)")

c_tbl, c_side = st.columns([3, 1])
with c_side:
    stops = sched[sched["stop_min"] > 0]
    st.metric("Planned stops", len(stops), f"{stops['stop_min'].sum():g} min total", delta_color="off")
    with st.form("quick_stop", clear_on_submit=True):
        st.markdown("**Quick-add a stop**")
        wp_pick = st.selectbox("At waypoint", sched["wp"].tolist(),
                               format_func=lambda w: f"WP {w} · mile {sched.loc[w, 'mile']:.2f}"
                               + (f" ({sched.loc[w, 'label']})" if sched.loc[w, "label"] else ""))
        mins = st.number_input("Minutes", 0.0, 240.0, 5.0, step=1.0)
        if st.form_submit_button("Set stop", width="stretch"):
            plan = store.load_plan()
            if mins:
                plan["stops"][int(wp_pick)] = float(mins)
            else:
                plan["stops"].pop(int(wp_pick), None)
            store.save_plan(plan)
            ss.editor_version += 1
            st.rerun()
    if ctx.plan["stops"] and st.button("Clear all stops", width="stretch"):
        store.update_plan(stops={})
        ss.editor_version += 1
        st.rerun()

with c_tbl:
    st.data_editor(
        table, key=editor_key, on_change=apply_edits, hide_index=True, width="stretch", height=460,
        disabled=[c for c in table.columns if c not in ("Stop (min)", "Note")],
        column_config={
            "Stop (min)": st.column_config.NumberColumn(min_value=0, max_value=240, step=1, format="%g"),
            "Mile": st.column_config.NumberColumn(format="%.2f"),
            "Elev (ft)": st.column_config.NumberColumn(format="%d"),
        },
    )
    st.download_button("Download schedule (CSV)", table.to_csv(index=False), "run_schedule.csv", "text/csv")
