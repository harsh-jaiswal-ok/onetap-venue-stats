"""Wasted-slot report.

Reads the snapshots the tracker has collected and works out, for every slot
whose start time has passed, whether it sold or went to waste. A slot counts as
WASTED when the last observation before its start time still showed it open.

Run any time:
    python report.py

Writes reports/TRACKING_REPORT.md, reports/TRACKING_REPORT.html and a
machine-readable reports/tracking_report.json, and prints a summary.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import store

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


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


def load_outcomes(db_path=store.DEFAULT_DB, now: datetime | None = None) -> tuple[list[SlotOutcome], dict]:
    now = now or datetime.now(timezone.utc)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    meta = {"venues": {}, "first_observed": None, "last_observed": None}

    with store.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT venue_id, venue_name, item_id, item_name, slot_start, slot_end, "
            "slot_local, observed_at, is_available, capacity, capacity_total FROM snapshots").fetchall()
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
        if start_utc > now:
            status = "pending"
        else:
            before = [o for o in obs if _parse_utc(o["observed_at"]) <= start_utc]
            if not before:
                status = "unobserved"
            else:
                final = before[-1]
                status = "wasted" if final["is_available"] else "sold"
                free_units = final["capacity"]
                total_units = final["capacity_total"]

        outcomes.append(SlotOutcome(
            venue_id, first["venue_name"], item_id, first["item_name"],
            first["slot_local"], start_utc, end_utc, weekday, hour, duration_h, status,
            free_units, total_units))

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
    heatmap: dict = field(default_factory=lambda: defaultdict(float))  # (weekday,hour) -> waste weight
    by_item: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))  # item -> [wasted, sold]

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
            if is_capacity:
                free = o.free_units or 0
                busy = (o.total_units or 0) - free
                s.idle_unit_hours += free * o.duration_h
                s.busy_unit_hours += busy * o.duration_h
                if o.weekday >= 0:
                    s.heatmap[(o.weekday, o.hour)] += free      # weight by idle units
            elif o.weekday >= 0:
                s.heatmap[(o.weekday, o.hour)] += 1
        elif o.status == "unobserved":
            s.unobserved += 1
        else:
            s.pending += 1
    return stats


def deadest_windows(s: VenueStats, top=5):
    ranked = sorted(s.heatmap.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [(WEEKDAYS[wd], hr, round(n)) for (wd, hr), n in ranked if wd >= 0]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_markdown(stats: dict[str, VenueStats], meta: dict, generated: str) -> str:
    L = ["# OneTap — Wasted-Slot Report", "",
         f"_Generated {generated}._", ""]
    window = ""
    if meta.get("first_observed"):
        window = (f"Tracking window: {meta['first_observed']} → {meta['last_observed']} "
                  f"({meta.get('poll_count', 0)} polls, {meta.get('poll_errors', 0)} errors).")
    L += [window, "",
          "For **slot venues**, a slot is **wasted** when the last check before its start still "
          "showed it open. For **capacity venues** (e.g. the driving range), we track **idle units** "
          "(free bays) per operating hour. **Utilisation** = booked ÷ total.", ""]

    L += ["| Venue | Type | Utilisation | Wasted / idle | Future |",
          "|-------|------|-------------|---------------|--------|"]
    for s in stats.values():
        if s.kind == "capacity":
            waste = f"{s.idle_unit_hours:.0f} idle bay-hrs"
        else:
            waste = f"{s.wasted} slots / {s.wasted_hours:.0f} hrs"
        L.append(f"| {s.venue_name} | {s.kind} | {s.utilization:.0f}% | {waste} | {s.pending} |")
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
    return "\n".join(L)


def to_json(stats: dict[str, VenueStats], meta: dict, generated: str) -> dict:
    return {
        "generated_at": generated,
        "tracking_window": {"first": meta.get("first_observed"), "last": meta.get("last_observed"),
                            "polls": meta.get("poll_count", 0), "errors": meta.get("poll_errors", 0)},
        "venues": [{
            "venue_id": s.venue_id, "venue_name": s.venue_name, "kind": s.kind,
            "sold": s.sold, "wasted": s.wasted, "unobserved": s.unobserved, "pending": s.pending,
            "utilisation_pct": round(s.utilization, 1), "wasted_hours": round(s.wasted_hours, 1),
            "total_units": s.total_units,
            "idle_unit_hours": round(s.idle_unit_hours, 1),
            "busy_unit_hours": round(s.busy_unit_hours, 1),
            "deadest_windows": [{"day": d, "hour": h, "wasted": n} for d, h, n in deadest_windows(s)],
            "by_item": {k: {"wasted": v[0], "sold": v[1]} for k, v in s.by_item.items()},
        } for s in stats.values()],
    }


def main():
    out = Path(__file__).parent / "reports"
    out.mkdir(exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
