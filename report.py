"""Wasted-slot report.

Reads the snapshots the tracker has collected and works out, for every slot
whose time has fully passed (its end time is in the past), whether it sold or
went to waste. A slot counts as WASTED when the last observation before its
start time still showed it open.

Run any time:
    python report.py

Writes reports/TRACKING_REPORT.md, reports/TRACKING_REPORT.html and a
machine-readable reports/tracking_report.json, and prints a summary.

All human-facing timestamps are shown in Sydney time.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import store

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SYDNEY = ZoneInfo("Australia/Sydney")


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def now_sydney_str() -> str:
    return datetime.now(SYDNEY).strftime("%Y-%m-%d %H:%M %Z")


def to_sydney_str(iso_utc: str | None) -> str | None:
    """Convert a stored UTC ISO timestamp to a Sydney-time display string."""
    if not iso_utc:
        return iso_utc
    try:
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso_utc
    return dt.astimezone(SYDNEY).strftime("%Y-%m-%d %H:%M %Z")


@dataclass
class SlotOutcome:
    venue_id: str
    venue_name: str
    item_id: str
    item_name: str
    slot_local: str          # "YYYY-MM-DD HH:MM" venue-local
    start_utc: datetime
    end_utc: datetime
    weekday: int
    hour: int
    duration_h: float
    status: str              # 'wasted' | 'sold' | 'unobserved' | 'pending'
    free_units: int | None = None    # capacity venues: free units at the last obs before start
    total_units: int | None = None   # capacity venues: total units
    price: float | None = None       # unit price (booking value or per-unit-hour)


def load_outcomes(db_path=store.DEFAULT_DB, now: datetime | None = None) -> tuple[list[SlotOutcome], dict]:
    now = now or datetime.now(timezone.utc)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    meta = {"venues": {}, "first_observed": None, "last_observed": None}

    with store.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT venue_id, venue_name, item_id, item_name, slot_start, slot_end, "
            "slot_local, observed_at, is_available, capacity, capacity_total, price FROM snapshots").fetchall()
        polls = conn.execute(
            "SELECT MIN(observed_at) a, MAX(observed_at) b, COUNT(DISTINCT observed_at) n, "
            "SUM(status='error') errs FROM poll_log").fetchone()

    if polls:
        meta["first_observed"] = polls["a"]
        meta["last_observed"] = polls["b"]
        meta["poll_count"] = polls["n"] or 0
        meta["poll_errors"] = polls["errs"] or 0

    for r in rows:
        key = (r["venue_id"], r["item_id"], r["slot_start"])
        groups[key].append(dict(r))

    outcomes: list[SlotOutcome] = []
    for (venue_id, item_id, slot_start), obs in groups.items():
        obs.sort(key=lambda o: o["observed_at"])
        first = obs[0]
        start_utc = _parse_utc(slot_start)
        end_utc = _parse_utc(first["slot_end"])
        duration_h = max(0.0, (end_utc - start_utc).total_seconds() / 3600)
        # local weekday/hour from stored wall-clock string
        try:
            local_dt = datetime.strptime(first["slot_local"], "%Y-%m-%d %H:%M")
            weekday, hour = local_dt.weekday(), local_dt.hour
        except ValueError:
            weekday, hour = -1, -1

        free_units = total_units = None
        is_capacity = first.get("capacity_total") is not None
        # Only score a slot once its time has fully passed (end time in the past).
        if end_utc > now:
            status = "pending"
        elif is_capacity:
            # Capacity venues (e.g. the range) expose ACTUAL bookings, including for
            # hours already finished, so the latest observation is the true final count.
            final = obs[-1]
            status = "wasted" if final["is_available"] else "sold"
            free_units = final["capacity"]
            total_units = final["capacity_total"]
        else:
            # Slot venues: use the last check before start. With the near-start poll
            # schedule this is ~5 min out, and the signal (has_customers) is unaffected
            # by the booking cutoff, so it reflects whether the slot got any bookings.
            before = [o for o in obs if _parse_utc(o["observed_at"]) <= start_utc]
            if not before:
                status = "unobserved"
            else:
                final = before[-1]
                status = "wasted" if final["is_available"] else "sold"

        outcomes.append(SlotOutcome(
            venue_id, first["venue_name"], item_id, first["item_name"],
            first["slot_local"], start_utc, end_utc, weekday, hour, duration_h, status,
            free_units, total_units, first["price"]))

    return outcomes, meta


@dataclass
class VenueStats:
    venue_id: str
    venue_name: str
    kind: str = "binary"     # "binary" (open/taken slots) | "capacity" (idle units per hour)
    sold: int = 0
    wasted: int = 0
    unobserved: int = 0
    pending: int = 0
    wasted_hours: float = 0.0
    # capacity venues only:
    idle_unit_hours: float = 0.0     # e.g. idle bay-hours
    busy_unit_hours: float = 0.0     # e.g. booked bay-hours
    total_units: int = 0
    wasted_money: float = 0.0        # estimated lost revenue from wasted slots
    heatmap: dict = field(default_factory=lambda: defaultdict(float))  # (weekday,hour) -> waste weight
    by_item: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))  # item -> [wasted, sold]
    wasted_events: list = field(default_factory=list)  # chronological wasted slots

    @property
    def decided(self) -> int:
        return self.sold + self.wasted

    @property
    def utilization(self) -> float:
        if self.kind == "capacity":
            total = self.idle_unit_hours + self.busy_unit_hours
            return (self.busy_unit_hours / total * 100) if total else 0.0
        return (self.sold / self.decided * 100) if self.decided else 0.0


def aggregate(outcomes: list[SlotOutcome]) -> dict[str, VenueStats]:
    stats: dict[str, VenueStats] = {}
    for o in outcomes:
        s = stats.get(o.venue_id)
        if s is None:
            s = stats[o.venue_id] = VenueStats(o.venue_id, o.venue_name)
        is_capacity = o.total_units is not None
        if is_capacity:
            s.kind = "capacity"
            s.total_units = max(s.total_units, o.total_units or 0)

        if o.status == "sold":
            s.sold += 1
            s.by_item[o.item_name][1] += 1
            if is_capacity:
                s.busy_unit_hours += (o.total_units or 0) * o.duration_h
        elif o.status == "wasted":
            s.wasted += 1
            s.wasted_hours += o.duration_h
            s.by_item[o.item_name][0] += 1
            free = o.free_units or 0
            if is_capacity:
                busy = (o.total_units or 0) - free
                s.idle_unit_hours += free * o.duration_h
                s.busy_unit_hours += busy * o.duration_h
                cost = free * (o.price or 0) * o.duration_h   # idle bays x per-bay-hour rate
                if o.weekday >= 0:
                    s.heatmap[(o.weekday, o.hour)] += free      # weight by idle units
            else:
                cost = o.price or 0
                if o.weekday >= 0:
                    s.heatmap[(o.weekday, o.hour)] += 1
            s.wasted_money += cost
            s.wasted_events.append({
                "sort": o.start_utc.isoformat(),
                "when": o.slot_local,          # Sydney wall-clock "YYYY-MM-DD HH:MM"
                "item": o.item_name,
                "idle": free if is_capacity else None,
                "cost": round(cost, 2),
            })
        elif o.status == "unobserved":
            s.unobserved += 1
        else:
            s.pending += 1
    return stats


def deadest_windows(s: VenueStats, top=5):
    ranked = sorted(s.heatmap.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [(WEEKDAYS[wd], hr, round(n)) for (wd, hr), n in ranked if wd >= 0]


def fmt_when(slot_local: str) -> str:
    """'2026-08-22 18:00' -> 'Sat 22 Aug, 6:00pm' (Sydney)."""
    try:
        dt = datetime.strptime(slot_local, "%Y-%m-%d %H:%M")
    except ValueError:
        return slot_local
    h12 = (dt.hour - 1) % 12 + 1
    ampm = "am" if dt.hour < 12 else "pm"
    return f"{dt.strftime('%a %d %b')}, {h12}:{dt.minute:02d}{ampm}"


def wasted_timeline(s: VenueStats, limit: int = 200) -> list[dict]:
    events = sorted(s.wasted_events, key=lambda e: e["sort"])[:limit]
    return [{"when": fmt_when(e["when"]), "item": e["item"], "idle": e["idle"],
             "cost": e["cost"]} for e in events]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_markdown(stats: dict[str, VenueStats], meta: dict, generated: str) -> str:
    L = ["# OneTap — Wasted-Slot Report", "",
         f"_Generated {generated}._", ""]
    window = ""
    if meta.get("first_observed"):
        window = (f"Tracking window: {to_sydney_str(meta['first_observed'])} → "
                  f"{to_sydney_str(meta['last_observed'])} "
                  f"({meta.get('poll_count', 0)} polls, {meta.get('poll_errors', 0)} errors).")
    total_money = sum(s.wasted_money for s in stats.values())
    L += [window, "",
          f"## 💸 Total wasted so far: **${total_money:,.0f} AUD**  *(estimated lost revenue)*", "",
          "For **slot venues**, a slot is **wasted** when the last check before its start still "
          "showed it open. For **capacity venues** (e.g. the driving range), we track **idle units** "
          "(free bays) per operating hour. **Utilisation** = booked ÷ total. Dollar figures are "
          "estimated lost revenue from configurable per-slot / per-bay-hour prices.", ""]

    L += ["| Venue | Type | Utilisation | Wasted / idle | Lost revenue | Future |",
          "|-------|------|-------------|---------------|--------------|--------|"]
    for s in stats.values():
        if s.kind == "capacity":
            waste = f"{s.idle_unit_hours:.0f} idle bay-hrs"
        else:
            waste = f"{s.wasted} slots / {s.wasted_hours:.0f} hrs"
        L.append(f"| {s.venue_name} | {s.kind} | {s.utilization:.0f}% | {waste} | "
                 f"${s.wasted_money:,.0f} | {s.pending} |")
    L.append("")

    for s in stats.values():
        L += [f"## {s.venue_name}", ""]
        decided_units = s.idle_unit_hours + s.busy_unit_hours if s.kind == "capacity" else s.decided
        if decided_units == 0:
            L += [f"No {'hours' if s.kind=='capacity' else 'slots'} have completed within the tracking "
                  f"window yet ({s.pending} future being watched, {s.unobserved} began before tracking). "
                  f"Leave the tracker running and re-run this report.", ""]
            continue
        if s.kind == "capacity":
            L += [f"- **Total bays:** {s.total_units}",
                  f"- **Occupancy:** {s.utilization:.0f}%  (booked bay-hours ÷ total bay-hours)",
                  f"- **Idle bay-hours:** {s.idle_unit_hours:.0f}  (booked: {s.busy_unit_hours:.0f})",
                  f"- **Estimated lost revenue:** ${s.wasted_money:,.0f}",
                  f"- **Future hours still watched:** {s.pending}", ""]
            dw = deadest_windows(s)
            if dw:
                L += ["**Deadest windows (most idle bays):**"]
                for day, hr, n in dw:
                    L.append(f"- {day} {hr:02d}:00 — ~{n} bays idle")
                L.append("")
        else:
            L += [f"- **Decided slots:** {s.decided}  ({s.sold} sold, {s.wasted} wasted)",
                  f"- **Utilisation:** {s.utilization:.0f}%",
                  f"- **Wasted session-hours:** {s.wasted_hours:.0f}",
                  f"- **Estimated lost revenue:** ${s.wasted_money:,.0f}",
                  f"- **Future slots still open:** {s.pending}", ""]
            dw = deadest_windows(s)
            if dw:
                L += ["**Deadest windows (most wasted slots):**"]
                for day, hr, n in dw:
                    L.append(f"- {day} {hr:02d}:00 — {n} wasted")
                L.append("")
            if s.by_item:
                L += ["**By offering:**"]
                for name, (w, sold) in sorted(s.by_item.items(), key=lambda kv: kv[1][0], reverse=True):
                    dec = w + sold
                    util = (sold / dec * 100) if dec else 0
                    L.append(f"- {name}: {w} wasted / {sold} sold ({util:.0f}% utilised)")
                L.append("")

        timeline = wasted_timeline(s)
        if timeline:
            noun = "idle-bay hours" if s.kind == "capacity" else "wasted slots"
            L += [f"**Every wasted slot, in order ({len(timeline)} {noun}, "
                  f"${s.wasted_money:,.0f} lost):**"]
            for e in timeline:
                what = f"{e['idle']} bays idle" if e["idle"] is not None else e["item"]
                L.append(f"- {e['when']} — {what} — **${e['cost']:,.0f}**")
            L.append("")
    return "\n".join(L)


def to_json(stats: dict[str, VenueStats], meta: dict, generated: str) -> dict:
    total_wasted_money = round(sum(s.wasted_money for s in stats.values()), 2)
    return {
        "generated_at": generated,
        "tracking_window": {"first": to_sydney_str(meta.get("first_observed")),
                            "last": to_sydney_str(meta.get("last_observed")),
                            "polls": meta.get("poll_count", 0), "errors": meta.get("poll_errors", 0)},
        "total_wasted_money": total_wasted_money,
        "currency": "AUD",
        "venues": [{
            "venue_id": s.venue_id, "venue_name": s.venue_name, "kind": s.kind,
            "sold": s.sold, "wasted": s.wasted, "unobserved": s.unobserved, "pending": s.pending,
            "utilisation_pct": round(s.utilization, 1), "wasted_hours": round(s.wasted_hours, 1),
            "wasted_money": round(s.wasted_money, 2),
            "total_units": s.total_units,
            "idle_unit_hours": round(s.idle_unit_hours, 1),
            "busy_unit_hours": round(s.busy_unit_hours, 1),
            "deadest_windows": [{"day": d, "hour": h, "wasted": n} for d, h, n in deadest_windows(s)],
            "by_item": {k: {"wasted": v[0], "sold": v[1]} for k, v in s.by_item.items()},
            "wasted_timeline": wasted_timeline(s),
        } for s in stats.values()],
    }


def main():
    out = Path(__file__).parent / "reports"
    out.mkdir(exist_ok=True)
    generated = now_sydney_str()

    outcomes, meta = load_outcomes()
    stats = aggregate(outcomes)

    data = to_json(stats, meta, generated)
    (out / "tracking_report.json").write_text(json.dumps(data, indent=2))
    (out / "TRACKING_REPORT.md").write_text(render_markdown(stats, meta, generated))
    try:
        import render_tracking_html
        (out / "TRACKING_REPORT.html").write_text(render_tracking_html.render(data))
    except Exception as exc:
        print(f"HTML render skipped: {exc!r}")

    total_wasted = sum(s.wasted for s in stats.values())
    total_sold = sum(s.sold for s in stats.values())
    print(f"Report written to {out / 'TRACKING_REPORT.md'}")
    print(f"Across {len(stats)} venue(s): {total_sold} sold, {total_wasted} wasted, "
          f"{sum(s.pending for s in stats.values())} future slots still being watched.")
    if not outcomes or (total_sold + total_wasted) == 0:
        print("No slots have completed within the tracking window yet — let the tracker run "
              "longer, then re-run report.py.")


if __name__ == "__main__":
    main()
