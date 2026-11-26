"""Outing Assistant: minimal Streamlit UI (form or chat) over the FastAPI backend."""
import datetime as dt
import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
INTERESTS = ["food", "music", "walks", "nature", "art", "shopping", "sports", "culture", "movies",
             "workshops", "games", "photography"]
CONSTRAINTS = ["vegetarian", "vegan", "avoid crowded places", "alcohol-free", "avoid outdoor activities",
               "avoid long walks", "family-friendly", "wheelchair accessible"]
FLEX = {"No, stay strictly within budget": "no", "Yes, if the improvement is worthwhile": "yes",
        "Show me the option and let me decide": "ask"}
ICON = {"success": "✅", "warning": "⚠️", "failed": "❌", "running": "⏳", "pending": "•",
        "pass": "✅", "warn": "⚠️", "fail": "❌"}

st.set_page_config(page_title="Outing Assistant", page_icon="🗓️", layout="wide")
for key, default in {"chat": [], "fields": {}, "resp": None, "trip": None, "finals": {}, "last": None,
                     "excluded": []}.items():
    st.session_state.setdefault(key, default)


# ---- backend ---------------------------------------------------------------

def call(path: str, payload: dict):
    try:
        r = httpx.post(f"{BACKEND_URL}{path}", json=payload, timeout=150)
    except httpx.HTTPError:
        return None, "I couldn't reach the planning service. Please try again in a moment."
    if r.status_code >= 400:
        try:
            return None, r.json().get("detail", r.text)
        except ValueError:
            return None, r.text
    return r.json(), None


def plan(req: dict, days: int = 1, fixed: float = 0.0, reset_exclusions: bool = True) -> None:
    """Single outing (days == 1) or multi-day trip. Remembers the request so 'Swap' can re-plan."""
    if reset_exclusions:
        st.session_state.excluded = []
    st.session_state.last = {"req": req, "days": days, "fixed": fixed}
    req = {**req, "excluded_venue_ids": st.session_state.excluded}
    with st.spinner("Planning: checking weather, finding places, costing and validating…"):
        if days > 1:
            data, err = call("/api/v1/trip", {"plan": req, "days": days, "fixed_costs": fixed})
        else:
            data, err = call("/api/v1/plan", req)
    st.session_state.finals = {}
    if err:
        st.error(err)
        return
    if days > 1:
        st.session_state.trip, st.session_state.resp = data, None
    else:
        st.session_state.resp, st.session_state.trip = data, None
        st.session_state.fields = data.get("partial", {})


def swap(venue_id: str) -> None:
    st.session_state.excluded = st.session_state.excluded + [venue_id]
    last = st.session_state.last
    plan(last["req"], last["days"], last["fixed"], reset_exclusions=False)


def hm(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).strftime("%H:%M")


def money(cur: str, v: float) -> str:
    return f"{cur}{v:,.0f}"


# ---- input -----------------------------------------------------------------

def next_saturday() -> dt.date:
    t = dt.date.today()
    return t + dt.timedelta(days=(5 - t.weekday()) % 7)


def form_tab() -> None:
    with st.form("prefs"):
        st.subheader("📍 Location")
        c1, c2, c3 = st.columns(3)
        city = c1.text_input("City *")
        start = c2.text_input("Starting point / neighbourhood")
        areas = c3.text_input("Preferred areas (comma separated)")
        travel = st.selectbox("Travel mode", ["flexible", "walking", "public_transport", "two_wheeler", "car", "taxi"])

        st.subheader("👥 Group")
        c1, c2, c3 = st.columns(3)
        gtype = c1.selectbox("Who is going?", ["friends", "couple", "solo", "family", "mixed"])
        size = c2.number_input("Number of people", 1, 50, 2)
        access = c3.text_input("Accessibility needs (optional)")

        st.subheader("💰 Budget")
        c1, c2, c3 = st.columns(3)
        budget = c1.number_input("Budget *", min_value=0, value=0, step=100)
        btype = c2.radio("Budget is", ["total", "per_person"], horizontal=True)
        flex = c3.selectbox("Would you spend up to 10% more for a significantly better experience?", list(FLEX))

        st.subheader("🕒 Timing")
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", next_saturday())
        mode = c2.radio("Timing", ["Exact times", "Tentative times", "Flexible period"], horizontal=True)
        c1, c2, c3, c4 = st.columns(4)
        s_t = c1.time_input("Start (exact/tentative)", dt.time(16, 0))
        e_t = c2.time_input("End (exact/tentative)", dt.time(20, 0))
        flex_min = c3.number_input("Flexibility (min, tentative)", 0, 240, 30, step=15)
        period = c4.selectbox("Period (flexible)", ["any", "morning", "afternoon", "evening", "night"])
        hours = st.number_input("Hours available (flexible period)", 0.5, 24.0, 4.0, step=0.5)
        c1, c2 = st.columns(2)
        days = c1.number_input("Days (1 = single outing, 2-5 = trip starting on the date above)", 1, 5, 1)
        fixed = c2.number_input("Trip fixed costs: lodging, intercity travel (trips only, optional)", 0, value=0, step=500)

        st.subheader("🎯 Mood, interests & constraints")
        mood = st.text_input("Mood", placeholder="e.g. tired but wants to do something fun")
        interests = st.multiselect("Interests", INTERESTS)
        constraints = st.multiselect("Constraints", CONSTRAINTS)
        other = st.text_input("Other constraints (comma separated)")
        notes = st.text_area("Anything else? (optional)", height=70)
        go = st.form_submit_button("Plan my outing", type="primary")

    if go:
        f: dict = {"city": city, "starting_location": start, "travel_mode": travel, "group_type": gtype,
                   "group_size": int(size), "budget": float(budget) or None, "budget_type": btype,
                   "budget_flexibility": FLEX[flex], "date": date.isoformat(), "mood": mood,
                   "preferred_areas": [a.strip() for a in areas.split(",") if a.strip()],
                   "accessibility": [access] if access.strip() else [], "interests": interests,
                   "constraints": constraints + [c.strip() for c in other.split(",") if c.strip()]}
        if mode == "Exact times":
            f.update(timing_mode="exact", start_time=s_t.strftime("%H:%M"), end_time=e_t.strftime("%H:%M"))
        elif mode == "Tentative times":
            f.update(timing_mode="tentative", start_time=s_t.strftime("%H:%M"), end_time=e_t.strftime("%H:%M"),
                     flexibility_minutes=int(flex_min))
        else:
            f.update(timing_mode="period", preferred_period=period, available_duration_minutes=int(hours * 60))
        plan({"fields": f, "message": notes or None, "message_overrides": False}, int(days), float(fixed))


def chat_tab() -> None:
    st.caption("Describe your outing in your own words: city, budget, time, mood, interests, constraints.")
    for m in st.session_state.chat:
        st.chat_message(m["role"]).write(m["content"])
    if text := st.chat_input("e.g. Saturday evening in Bangalore, ₹2000 for 2 friends, vegetarian, love music and walks"):
        st.session_state.chat.append({"role": "user", "content": text})
        plan({"fields": st.session_state.fields, "message": text, "message_overrides": True})
        r = st.session_state.resp
        if r:
            reply = {"needs_clarification": "\n\n".join([r["message"], *[f"- {q}" for q in r["questions"]]]),
                     "fallback": r["message"],
                     "proposal": f"{r['message']} See the plans below."}[r["status"]]
            st.session_state.chat.append({"role": "assistant", "content": reply})
        st.rerun()


# ---- results ---------------------------------------------------------------

def show_map(it: dict) -> None:
    pts = [{"lat": s["venue"]["lat"], "lon": s["venue"]["lon"]} for s in it["stops"]]
    if it.get("origin"):
        pts.insert(0, {"lat": it["origin"]["lat"], "lon": it["origin"]["lon"]})
    st.map(pts, size=40, zoom=13)
    if it.get("route_url"):
        st.link_button("Open the whole route in Google Maps", it["route_url"])


def show_proposal(r: dict, p: dict, key: str) -> None:
    cur, cost, it = r["currency"], p["cost"], p["itinerary"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Estimated total", money(cur, cost["total"]))
    c2.metric("Per person", money(cur, cost["per_person"]))
    c3.metric("Your budget", money(cur, cost["budget"]),
              delta=f"{money(cur, cost['over_by'])} over" if cost["over_by"] else "within budget",
              delta_color="inverse" if cost["over_by"] else "normal")
    for t in p["tradeoffs"]:
        st.info(t)
    st.markdown("#### Timeline")
    for s in it["stops"]:
        v = s["venue"]
        route = s.get("route")
        with st.container(border=True):
            top, nav, btn = st.columns([6, 1, 1])
            top.markdown(f"**{hm(s['start'])}-{hm(s['end'])} · {'🍽️' if v['kind'] == 'food' else '🎯'} {v['name']}**  \n"
                         f"{v.get('address') or ''}")
            nav.link_button("Go", f"https://www.google.com/maps/dir/?api=1&destination={v['lat']},{v['lon']}")
            if btn.button("Swap", key=f"swap-{key}-{p['id']}-{v['id']}", help="Plan again without this place"):
                swap(v["id"])
                st.rerun()
            if s["why"]:
                st.write(s["why"])
            bits = []
            if route:
                bits.append(f"🚗 {route['duration_min']:.0f} min {route['mode'].replace('_', ' ')}, {route['distance_km']:.1f} km "
                            f"({'estimate' if route['source'] == 'estimate' else 'traffic-aware'})")
            if s.get("availability"):
                a = s["availability"]
                bits.append({"open": "🟢 open per provider hours", "closed": "🔴 closed",
                             "unverified": "🟡 hours unverified"}[a["status"]])
            if s.get("crowd"):
                bits.append(f"👥 crowd: {s['crowd']['level']} (heuristic)")
            st.caption(" · ".join(bits))
    for n in it["notes"]:
        st.caption(f"ℹ️ {n}")
    with st.expander("🗺️ Map and route", expanded=False):
        show_map(it)

    left, right = st.columns(2)
    with left:
        st.markdown("#### Cost breakdown (estimates)")
        st.table([{"Item": l["label"], "Amount": money(cur, l["amount"])} for l in cost["lines"]])
    with right:
        st.markdown("#### Checks")
        for c in p["validation"]["checks"]:
            st.markdown(f"{ICON[c['status']]} {c['message']}")

    st.divider()
    accept = False
    if p["requires_budget_approval"]:
        accept = st.checkbox(f"I approve spending {money(cur, cost['over_by'])} over my budget (within the 10% limit)",
                             key=f"accept-{key}-{p['id']}")
    if st.button(f"Approve “{p['title']}”", type="primary", key=f"approve-{key}-{p['id']}",
                 disabled=p["requires_budget_approval"] and not accept):
        with st.spinner("Finalising your plan, this can take 10-20 seconds. Please don't click again."):
            data, err = call("/api/v1/finalize", {"preferences": r["preferences"], "proposal": p, "temporal": r["temporal"],
                                                  "weather": r["weather"], "approved": True, "accept_extra_budget": accept})
        if err:
            st.error(err)
        else:
            st.session_state.finals[key] = data
            st.rerun()


def show_final(f: dict, key: str) -> None:
    st.success("Plan approved. Nothing has been booked: use the notes and links below.")
    st.write(f["overview"])
    for t in f["timeline"]:
        with st.container(border=True):
            st.markdown(f"**{t['time']} · {t['title']}**  \n{t.get('address') or ''}")
            if t.get("why"):
                st.write(t["why"])
            if t.get("travel_note"):
                st.caption(f"Getting there: {t['travel_note']}")
            if t.get("map_link"):
                st.link_button("Navigate", t["map_link"], key=f"nav-{key}-{t['time']}")
    c1, c2, c3 = st.columns(3)
    if f.get("route_link"):
        c1.link_button("🗺️ Full route", f["route_link"])
    if f.get("calendar_link"):
        c2.link_button("📅 Google Calendar", f["calendar_link"])
    if f.get("ics"):
        c3.download_button("⬇️ Add to calendar (.ics)", f["ics"], file_name=f"outing-{key}.ics",
                           mime="text/calendar", key=f"ics-{key}")
    st.metric("Estimated total", money(f["currency"], f["total_cost"]), f"{money(f['currency'], f['per_person'])} per person",
              delta_color="off")
    st.markdown("**Practical notes**")
    for n in f["practical_notes"]:
        st.write(f"- {n}")
    st.markdown("**Caveats**")
    for c in f["caveats"]:
        st.write(f"- {c}")


def show_response(r: dict, key: str) -> None:
    """One day's (or the single outing's) result: clarification, fallback, proposals or the final plan."""
    if r["status"] == "needs_clarification":
        st.info(r["message"])
        for q in r["questions"]:
            st.write(f"- {q}")
    elif r["status"] == "fallback":
        st.warning(r["message"])
        for s in r["generic_suggestions"]:
            st.write(f"- {s}")
    else:
        t, w = r["temporal"], r.get("weather")
        st.caption(("Weather: " + w["summary"] + " (Open-Meteo)") if w and w["available"] else "Weather: unavailable")
        for a in r["assumptions"]:
            st.caption(f"Assumption: {a}")
        if key in st.session_state.finals:
            show_final(st.session_state.finals[key], key)
        else:
            rec, props = r["recommended_id"], r["proposals"]
            tabs = st.tabs([("⭐ " if p["id"] == rec else "") + p["title"] for p in props])
            for tab, p in zip(tabs, props):
                with tab:
                    show_proposal(r, p, key)
    if r["llm"] and not any(i["used"] for i in r["llm"]):
        st.caption("AI language features were unavailable, so deterministic logic was used (reduced AI functionality).")


def show_trip(trip: dict) -> None:
    cur = trip["currency"]
    if trip["status"] == "needs_clarification":
        st.info(trip["message"])
        for q in trip["questions"]:
            st.write(f"- {q}")
        return
    st.subheader(f"Your {len(trip['days'])}-day trip")
    c1, c2, c3 = st.columns(3)
    c1.metric("Trip budget", money(cur, trip["trip_budget"]))
    c2.metric("Estimated total (recommended plans + fixed costs)", money(cur, trip["estimated_total"]),
              delta="within budget" if trip["within_budget"] else "over budget",
              delta_color="normal" if trip["within_budget"] else "inverse")
    c3.metric("Fixed costs you entered", money(cur, trip["fixed_costs"]))
    st.write(trip["message"])
    tabs = st.tabs([f"Day {d['day']} · {dt.date.fromisoformat(d['date']):%a %d %b}" for d in trip["days"]])
    for tab, d in zip(tabs, trip["days"]):
        with tab:
            show_response(d["response"], f"day{d['day']}")


def show_results() -> None:
    st.divider()
    if st.session_state.trip:
        show_trip(st.session_state.trip)
    elif (r := st.session_state.resp):
        if r["status"] == "proposal":
            st.subheader(f"Your outing on {dt.date.fromisoformat(r['temporal']['date']):%A %d %b %Y}")
        show_response(r, "single")


st.title("🗓️ Outing Assistant")
st.caption("Less planning. More living.")
tab_form, tab_chat = st.tabs(["📝 Fill a form", "💬 Chat with the assistant"])
with tab_form:
    form_tab()
with tab_chat:
    chat_tab()
show_results()
