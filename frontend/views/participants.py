"""Participants page: who is coming, where they merge/depart, and head-count along the route."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import common as C
from common import sch, store

ctx: C.Ctx = st.session_state["ctx"]
people = store.load_participants()

st.title("👥 Participants")
st.caption(f"{ctx.route['name']} · start {C.fmt_t(ctx.start)} · finish ≈ {C.fmt_t(ctx.finish['arrive'])}. "
           "Add yourself from the Route plan page.")

counts = {k: sum(p["plan"] == k for p in people) for k in C.PLAN_LABELS}
m = st.columns(4)
m[0].metric("Total", len(people))
m[1].metric("Whole route", counts["full"])
m[2].metric("Part of the route", counts["part"])
m[3].metric("Meeting at finish", counts["finish"])

if not people:
    st.info("No one has signed up yet. Use the **Join the run** form on the Route plan page.")
    st.stop()

rows = []
for p in sorted(people, key=lambda p: p["name"].casefold()):
    win = ctx.participant_window(p)
    if win:
        j, l = win
        t_in, t_out = ctx.time_at(j), ctx.time_at(l)
        rows.append({"Name": p["name"], "Plan": C.PLAN_LABELS[p["plan"]], "Merge mile": j, "Merge time": C.fmt_t(t_in),
                     "Depart mile": l, "Depart time": C.fmt_t(t_out), "Distance (mi)": l - j,
                     "With group": sch.fmt_duration((t_out - t_in).total_seconds())})
    else:
        rows.append({"Name": p["name"], "Plan": C.PLAN_LABELS[p["plan"]], "Merge mile": None, "Merge time": "—",
                     "Depart mile": None, "Depart time": f"At finish {C.fmt_t(ctx.finish['arrive'])}",
                     "Distance (mi)": 0.0, "With group": "—"})

st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
    "Merge mile": st.column_config.NumberColumn(format="%.2f"),
    "Depart mile": st.column_config.NumberColumn(format="%.2f"),
    "Distance (mi)": st.column_config.NumberColumn(format="%.2f"),
})

with st.expander("Remove participants"):
    by_name = {p["name"]: p["id"] for p in people}
    gone = st.multiselect("Select people to remove", sorted(by_name, key=str.casefold))
    if st.button("Remove selected", disabled=not gone):
        store.remove_participants([by_name[n] for n in gone])
        st.rerun()

st.subheader("Runners along the route")
cov = sch.segment_coverage(ctx.wps, people, ctx.total_mi)
chart_df = pd.DataFrame([{"Mile": c["m0"], "Runners": c["count"]} for c in cov]
                        + [{"Mile": cov[-1]["m1"], "Runners": cov[-1]["count"]}])
st.altair_chart(alt.Chart(chart_df).mark_area(interpolate="step-after", opacity=0.5, line=True, color=C.ORANGE)
                .encode(x=alt.X("Mile:Q"), y=alt.Y("Runners:Q", axis=alt.Axis(tickMinStep=1)),
                        tooltip=[alt.Tooltip("Mile:Q", format=".2f"), "Runners:Q"])
                .properties(height=180), width="stretch")

st_folium(C.build_map(ctx, people), key="people_map", height=520, use_container_width=True, returned_objects=[])
st.caption("Line thickness = number of participants on that stretch · hover a stretch to see who is on it.")
