"""Build a static stats website into docs/ for GitHub Pages.

Combines two data sources into one self-contained page (data rendered inline,
no fetch needed — works on GitHub Pages and when opened locally):

  reports/report_data.json      -> Venue intelligence (booking platforms, prices, hours)
  reports/tracking_report.json  -> Idle inventory (wasted vs sold slots)

Usage:
    python build_site.py        # writes docs/index.html (+ .nojekyll)

Refresh flow: run venue_report.py / report.py to regenerate the JSON, then
re-run this, then commit docs/.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------------------
# Styles — one shared token system + components for both sections
# ---------------------------------------------------------------------------

CSS = """
:root{
  --paper:#F6F3E8; --panel:#FDFBF3; --row:#ECE8D9; --ink:#17251D; --muted:#5B6B60;
  --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#1E8A56; --green-bright:#2BB673;
  --pill-bg:#DDEFE2; --warn:#C25B3A; --warn-bg:#F3E1D9; --warn-ink:#8A4A2E;
  --line:#D8D2BE; --bar-bg:#E4DFCD;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --paper:#0F1712; --panel:#16211A; --row:#1B281F; --ink:#E9E6D5; --muted:#93A398;
  --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#4CC98A; --green-bright:#2BB673;
  --pill-bg:#1E3A2B; --warn:#E08763; --warn-bg:#3A2418; --warn-ink:#E0B678;
  --line:#2A3A30; --bar-bg:#22301F; }}
:root[data-theme="dark"]{
  --paper:#0F1712; --panel:#16211A; --row:#1B281F; --ink:#E9E6D5; --muted:#93A398;
  --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#4CC98A; --green-bright:#2BB673;
  --pill-bg:#1E3A2B; --warn:#E08763; --warn-bg:#3A2418; --warn-ink:#E0B678;
  --line:#2A3A30; --bar-bg:#22301F; }
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{background:var(--paper);color:var(--ink);margin:0;line-height:1.55;
  font-family:'Archivo','Helvetica Neue',Arial,sans-serif}
.mono{font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace}
.num{font-variant-numeric:tabular-nums}
.wrap{max-width:64rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
a{color:var(--green)}
header.mast{border-bottom:3px solid var(--deep);padding-bottom:1.5rem;margin-bottom:1.5rem;
  display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap}
.brand{font-family:'IBM Plex Mono',monospace;font-weight:700;font-size:1rem}.brand .t{color:var(--green)}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:.68rem;font-weight:600;letter-spacing:.18em;
  text-transform:uppercase;color:var(--green);margin:.5rem 0 .75rem}
h1{font-size:clamp(1.9rem,5vw,2.9rem);font-weight:800;letter-spacing:-.025em;margin:0 0 .5rem;text-wrap:balance}
.lede{color:var(--muted);max-width:44rem;margin:0}
.themebtn{font-family:'IBM Plex Mono',monospace;font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;
  background:transparent;color:var(--muted);border:1px solid var(--line);padding:.45rem .7rem;cursor:pointer}
.themebtn:hover{color:var(--green);border-color:var(--green)}
/* summary tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.75rem;margin:1.5rem 0}
.tile{background:var(--panel);border:1px solid var(--line);padding:1rem 1.1rem}
.tile .n{font-size:1.9rem;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.tile .n.warn{color:var(--warn)}.tile .n.good{color:var(--green)}
.tile .k{font-family:'IBM Plex Mono',monospace;font-size:.6rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted);margin-top:.4rem}
.moneybar{background:var(--deep);color:var(--deep-ink);padding:1.5rem 1.75rem;margin:1.5rem 0 0;
  border-radius:2px}
.moneybar .ml{font-family:'IBM Plex Mono',monospace;font-size:.68rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--green-bright);margin-bottom:.3rem}
.moneybar .mv{font-size:clamp(2.4rem,7vw,3.8rem);font-weight:800;line-height:1;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}
.moneybar .mc{font-size:1rem;font-weight:600;color:var(--green-bright)}
.moneybar .mn{font-size:.82rem;color:var(--deep-ink);opacity:.7;margin-top:.5rem}
.money,td.money{color:var(--warn);font-weight:600}
/* tabs */
.tabs{display:flex;gap:.25rem;border-bottom:1px solid var(--line);margin:2rem 0 1.5rem;flex-wrap:wrap}
.tab{font-family:'IBM Plex Mono',monospace;font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;
  font-weight:600;background:transparent;border:none;border-bottom:2px solid transparent;color:var(--muted);
  padding:.7rem 1rem;cursor:pointer;margin-bottom:-1px}
.tab[aria-selected="true"]{color:var(--green);border-bottom-color:var(--green)}
.panel[hidden]{display:none}
/* tables */
.tablebox{overflow-x:auto;border:1px solid var(--line);margin:1.25rem 0}
table{border-collapse:collapse;width:100%;font-size:.85rem;min-width:40rem}
thead th{background:var(--deep);color:var(--deep-ink);text-align:left;font-family:'IBM Plex Mono',monospace;
  font-size:.62rem;letter-spacing:.12em;text-transform:uppercase;padding:.6rem .75rem;white-space:nowrap}
tbody td{padding:.6rem .75rem;border-top:1px solid var(--line);vertical-align:top}
tbody tr:nth-child(even){background:var(--row)}
.util-cell{display:flex;align-items:center;gap:.5rem;min-width:8rem}
.util-track{flex:1;height:7px;background:var(--bar-bg);position:relative;overflow:hidden;min-width:3rem}
.util-fill{position:absolute;inset:0 auto 0 0;background:var(--green)}
/* venue cards */
.card{border:1px solid var(--line);background:var(--panel);margin-bottom:1.5rem;padding:1.25rem}
.card h2,.card h3.vt{margin:0;font-size:1.3rem;font-weight:800;letter-spacing:-.01em}
.vhead{display:flex;align-items:baseline;gap:.75rem;flex-wrap:wrap;margin-bottom:.15rem}
.vnum{font-family:'IBM Plex Mono',monospace;color:var(--green);font-weight:700}
.suburb{color:var(--muted);font-size:.88rem}
.pill{font-family:'IBM Plex Mono',monospace;font-size:.62rem;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;padding:.22rem .55rem;background:var(--pill-bg);color:var(--green);white-space:nowrap}
.pill.warn{background:var(--warn-bg);color:var(--warn-ink)}.pill.star{background:var(--deep);color:var(--deep-ink)}
.statrow{display:flex;flex-wrap:wrap;gap:1.4rem;margin:.8rem 0 1rem}
.stat .n{font-size:1.5rem;font-weight:800;font-variant-numeric:tabular-nums}
.stat .n.warn{color:var(--warn)}.stat .n.good{color:var(--green)}
.stat .k{font-family:'IBM Plex Mono',monospace;font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:.5rem 1.4rem;margin:.5rem 0 .5rem}
.fact .k{font-family:'IBM Plex Mono',monospace;font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.fact .v{font-size:.88rem}
.block h4{font-family:'IBM Plex Mono',monospace;font-size:.62rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--green);margin:1rem 0 .5rem;border-top:1px solid var(--line);padding-top:.7rem}
.chips{display:flex;flex-wrap:wrap;gap:.35rem}
.chip{font-size:.76rem;padding:.22rem .55rem;border:1px solid var(--line);background:var(--row)}
ul.data{list-style:none;margin:0;padding:0;display:grid;gap:.25rem}
ul.data li{font-family:'IBM Plex Mono',monospace;font-size:.76rem;padding-left:1rem;position:relative}
ul.data li::before{content:"·";position:absolute;left:0;color:var(--green);font-weight:700}
.note{background:var(--warn-bg);color:var(--warn-ink);padding:.65rem .9rem;font-size:.84rem;margin:.5rem 0}
.empty{color:var(--muted);font-size:.9rem}
details summary{cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:.62rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted)}
details ul{margin:.4rem 0 0;padding-left:1.1rem}details a{word-break:break-all}
.heat{overflow-x:auto;margin-top:.4rem}
.heat table{min-width:32rem;font-size:.7rem}
.heat th,.heat td{border:1px solid var(--line);padding:.28rem;text-align:center;font-variant-numeric:tabular-nums}
.heat th{background:transparent;color:var(--muted);font-family:'IBM Plex Mono',monospace;font-weight:600;letter-spacing:normal;text-transform:none}
.itemgrid{display:grid;gap:.4rem}
.item{display:grid;grid-template-columns:1fr auto;gap:.4rem .5rem;align-items:center;font-size:.84rem}
.item .bar{grid-column:1/-1;height:6px;background:var(--bar-bg);position:relative;overflow:hidden}
.item .bar i{position:absolute;inset:0 auto 0 0;background:var(--green)}
.timeline{max-height:20rem;overflow-y:auto;border:1px solid var(--line)}
.timeline table{min-width:0;width:100%;font-size:.82rem}
.timeline td{padding:.35rem .7rem;border-top:1px solid var(--line)}
.timeline tr:first-child td{border-top:none}
.timeline tr:nth-child(even){background:var(--row)}
.timeline td:first-child{white-space:nowrap;color:var(--muted)}
.rule{background:var(--green-bright);color:#0B2417;padding:.8rem 1rem;font-size:.85rem;margin:1.5rem 0}
.rule strong{font-weight:800}
footer{margin-top:3rem;border-top:1px solid var(--line);padding-top:1rem;display:flex;justify-content:space-between;
  gap:1rem;flex-wrap:wrap;font-family:'IBM Plex Mono',monospace;font-size:.64rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted)}
"""


# ---------------------------------------------------------------------------
# Idle-inventory (tracking) section
# ---------------------------------------------------------------------------

def heatcolor(n, mx):
    if n == 0 or mx == 0:
        return "transparent"
    t = 0.15 + 0.85 * (n / mx)
    return f"color-mix(in srgb, var(--warn) {int(t*100)}%, transparent)"


def render_heatmap(windows):
    if not windows:
        return ""
    by = {(w["day"], w["hour"]): w["wasted"] for w in windows}
    hours = sorted({w["hour"] for w in windows})
    mx = max(by.values())
    head = "".join(f"<th>{h:02d}</th>" for h in hours)
    rows = []
    for d in WEEKDAYS:
        if not any((d, h) in by for h in hours):
            continue
        cells = "".join(
            f'<td style="background:{heatcolor(by.get((d,h),0),mx)}">{by.get((d,h)) or ""}</td>'
            for h in hours)
        rows.append(f"<tr><th>{d}</th>{cells}</tr>")
    return (f'<div class="heat"><table><thead><tr><th></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def render_timeline(v):
    """Chronological list of every wasted slot with its Sydney time."""
    tl = v.get("wasted_timeline") or []
    if not tl:
        return ""
    noun = "idle-bay hours" if v.get("kind") == "capacity" else "wasted slots"
    rows = []
    for e in tl:
        detail = f'{e["idle"]} bays idle' if e.get("idle") is not None else esc(e["item"])
        cost = e.get("cost")
        cost_cell = f'<td class="num money">${cost:,.0f}</td>' if cost is not None else '<td></td>'
        rows.append(f'<tr><td class="mono">{esc(e["when"])}</td><td>{detail}</td>{cost_cell}</tr>')
    lost = v.get("wasted_money", 0)
    return (f'<div class="block"><h4>Every wasted slot, in order ({len(tl)} {noun} · '
            f'${lost:,.0f} lost)</h4>'
            f'<div class="timeline"><table><tbody>{"".join(rows)}</tbody></table></div></div>')


def render_tracking_venue(v):
    kind = v.get("kind", "binary")
    util = v["utilisation_pct"]

    if kind == "capacity":
        decided = v["idle_unit_hours"] + v["busy_unit_hours"]
        if decided == 0:
            return (f'<div class="card"><h2>{esc(v["venue_name"])}</h2>'
                    f'<p class="empty">No operating hours have completed within the tracking window yet — '
                    f'{v["pending"]} future hours are being watched. Leave the tracker running and rebuild.</p></div>')
        stats = f"""
          <div class="stat"><div class="n warn num">${v['wasted_money']:,.0f}</div><div class="k">Lost revenue</div></div>
          <div class="stat"><div class="n warn num">{v['idle_unit_hours']:.0f}</div><div class="k">Idle bay-hours</div></div>
          <div class="stat"><div class="n num">{util:.0f}%</div><div class="k">Occupancy</div></div>
          <div class="stat"><div class="n good num">{v['busy_unit_hours']:.0f}</div><div class="k">Booked bay-hours</div></div>
          <div class="stat"><div class="n num">{v['pending']}</div><div class="k">Hours ahead</div></div>"""
        heat = render_heatmap(v["deadest_windows"])
        heat_block = (f'<div class="block"><h4>Deadest windows · idle bays by day &amp; hour</h4>{heat}</div>'
                      if heat else "")
        return f"""<div class="card"><h2>{esc(v['venue_name'])}</h2>
          <div class="suburb">{v['total_units']} bays · idle-inventory tracking</div>
          <div class="statrow">{stats}</div>{heat_block}{render_timeline(v)}</div>"""

    decided = v["sold"] + v["wasted"]
    if decided == 0:
        return (f'<div class="card"><h2>{esc(v["venue_name"])}</h2>'
                f'<p class="empty">No slots have finished within the tracking window yet — '
                f'{v["pending"]} future slots are being watched. Leave the tracker running and rebuild.</p></div>')
    stats = f"""
      <div class="stat"><div class="n warn num">${v['wasted_money']:,.0f}</div><div class="k">Lost revenue</div></div>
      <div class="stat"><div class="n warn num">{v['wasted']}</div><div class="k">Wasted slots</div></div>
      <div class="stat"><div class="n good num">{v['sold']}</div><div class="k">Sold slots</div></div>
      <div class="stat"><div class="n num">{util:.0f}%</div><div class="k">Utilisation</div></div>
      <div class="stat"><div class="n num">{v['pending']}</div><div class="k">Open (future)</div></div>"""
    heat = render_heatmap(v["deadest_windows"])
    heat_block = f'<div class="block"><h4>Deadest windows · wasted slots by day &amp; hour</h4>{heat}</div>' if heat else ""
    items = v["by_item"]
    items_block = ""
    if items:
        mx = max((d["wasted"] for d in items.values()), default=1) or 1
        rows = []
        for name, d in sorted(items.items(), key=lambda kv: kv[1]["wasted"], reverse=True):
            dec = d["wasted"] + d["sold"]
            iu = (d["sold"] / dec * 100) if dec else 0
            rows.append(
                f'<div class="item"><span>{esc(name)}</span>'
                f'<span class="mono num">{d["wasted"]} wasted · {d["sold"]} sold · {iu:.0f}%</span>'
                f'<span class="bar"><i style="width:{d["wasted"]/mx*100:.0f}%"></i></span></div>')
        items_block = f'<div class="block"><h4>By offering</h4><div class="itemgrid">{"".join(rows)}</div></div>'
    return f"""<div class="card"><h2>{esc(v['venue_name'])}</h2>
      <div class="suburb">{decided} decided slots tracked</div>
      <div class="statrow">{stats}</div>{heat_block}{items_block}{render_timeline(v)}</div>"""


def render_tracking_panel(tracking):
    if not tracking or not tracking.get("venues"):
        return ('<p class="empty">No tracking data yet. Start <code>tracker.py</code>, let it run, '
                'run <code>report.py</code>, then rebuild the site.</p>')
    venues = tracking["venues"]
    tw = tracking.get("tracking_window", {})
    rows = []
    for v in venues:
        util = v["utilisation_pct"]
        if v.get("kind") == "capacity":
            waste_cell = f'{v["idle_unit_hours"]:.0f} idle bay-hrs'
            util_label = "occupancy"
        else:
            waste_cell = f'{v["wasted"]} / {v["wasted_hours"]:.0f} hrs'
            util_label = "utilisation"
        rows.append(
            f'<tr><td><b>{esc(v["venue_name"])}</b></td>'
            f'<td class="num money">${v.get("wasted_money",0):,.0f}</td>'
            f'<td class="num">{util:.0f}%<br><span class="suburb">{util_label}</span></td>'
            f'<td><div class="util-cell"><span class="util-track">'
            f'<span class="util-fill" style="width:{util:.0f}%"></span></span></div></td>'
            f'<td class="num">{esc(waste_cell)}</td><td class="num">{v["pending"]}</td></tr>')
    window = ""
    if tw.get("first"):
        window = (f'<p class="lede">Tracking window {esc(tw["first"])} → {esc(tw["last"])} · '
                  f'{tw.get("polls",0)} polls · {tw.get("errors",0)} errors.</p>')
    intro = ('<p class="lede">For <b>slot venues</b> (Kiss My Axe), a slot is <b>wasted</b> when it had '
             '<b>zero bookings</b> by its start time — a session that ran empty. For <b>capacity venues</b> '
             '(the driving range) we read the <b>actual bookings</b> and count <b>idle bays</b> per '
             'operating hour. <b>Lost revenue</b> is estimated from configurable prices.</p>')
    table = (f'<div class="tablebox"><table><thead><tr><th>Venue</th><th>Lost revenue</th>'
             f'<th>Utilisation</th><th></th><th>Wasted / idle</th><th>Future</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    cards = "".join(render_tracking_venue(v) for v in venues)
    return intro + window + table + cards


# ---------------------------------------------------------------------------
# Venue-intelligence section
# ---------------------------------------------------------------------------

def render_intel_venue(i, f):
    v = f["venue"]
    ok = f["status"] == "ok"
    pills = []
    if v.get("first_visit"):
        pills.append('<span class="pill star">★ first visit</span>')
    if f["booking_platforms"]:
        pills.append(f'<span class="pill">{esc(", ".join(f["booking_platforms"]))}</span>')
    elif ok:
        pills.append('<span class="pill warn">no platform detected</span>')
    else:
        pills.append('<span class="pill warn">site unreachable</span>')
    facts = [("Activity", esc(v["activity"])), ("What gets booked", esc(v["what_gets_booked"])),
             ("Walk group", esc(v["walk"]))]
    site = f.get("final_url") or v["url"]
    facts.append(("Website", f'<a href="{esc(site)}" rel="noopener">{esc(site.replace("https://","").rstrip("/"))}</a>'))
    if f["phones"]:
        facts.append(("Phone", esc(" / ".join(f["phones"][:2]))))
    if f["emails"]:
        facts.append(("Email", esc(" / ".join(f["emails"][:2]))))
    facts_html = "".join(f'<div class="fact"><div class="k">{k}</div><div class="v">{val}</div></div>'
                         for k, val in facts)
    blocks = []
    if v.get("notes"):
        blocks.append(f'<div class="note"><b>Research notes:</b> {esc(v["notes"])}</div>')
    if ok:
        if f["booking_platforms"]:
            blocks.append(f'<div class="block"><h4>Booking stack</h4><p>Detected: '
                          f'<b>{esc(", ".join(f["booking_platforms"]))}</b>.</p></div>')
        else:
            blocks.append('<div class="block"><h4>Booking stack</h4><p>No third-party platform detected — '
                          'phone/email or custom. <b>Good sign for a pitch.</b></p></div>')
        if f["offerings"]:
            chips = "".join(f'<span class="chip">{esc(o)}</span>' for o in f["offerings"])
            blocks.append(f'<div class="block"><h4>Bookable offerings</h4><div class="chips">{chips}</div></div>')
        if f["price_mentions"]:
            items = "".join(f"<li>{esc(p)}</li>" for p in f["price_mentions"])
            blocks.append(f'<div class="block"><h4>Pricing signals ({len(f["price_mentions"])})</h4>'
                          f'<ul class="data">{items}</ul></div>')
        if f["hours_mentions"]:
            items = "".join(f"<li>{esc(h)}</li>" for h in f["hours_mentions"])
            blocks.append(f'<div class="block"><h4>Opening hours</h4><ul class="data">{items}</ul></div>')
        if f["pages_crawled"]:
            pages = "".join(f'<li><a href="{esc(p)}" rel="noopener">{esc(p)}</a></li>' for p in f["pages_crawled"])
            blocks.append(f'<details><summary>{len(f["pages_crawled"])} pages crawled</summary><ul>{pages}</ul></details>')
    else:
        blocks.append(f'<div class="block"><h4>Crawl status</h4><p>Unreachable: {esc(f.get("error") or "")}. '
                      'Verify manually.</p></div>')
    return f"""<div class="card">
      <div class="vhead"><span class="vnum">{i:02d}</span><h3 class="vt">{esc(v['name'])}</h3>
        <span class="suburb">{esc(v['suburb'])}</span>{"".join(pills)}</div>
      <div class="facts">{facts_html}</div>{"".join(blocks)}</div>"""


def render_intel_panel(intel):
    if not intel or not intel.get("venues"):
        return '<p class="empty">No venue-intelligence data. Run <code>venue_report.py</code> and rebuild.</p>'
    venues = intel["venues"]
    rows = []
    for i, f in enumerate(venues, 1):
        v = f["venue"]
        ok = f["status"] == "ok"
        platform = ", ".join(f["booking_platforms"]) or ("—" if ok else "unreachable")
        contact = f["phones"][0] if f["phones"] else (f["emails"][0] if f["emails"] else "—")
        star = " ★" if v.get("first_visit") else ""
        rows.append(f'<tr><td class="mono" style="color:var(--green)">{i:02d}</td>'
                    f'<td><b>{esc(v["name"])}{star}</b><br><span class="suburb">{esc(v["suburb"])}</span></td>'
                    f'<td>{esc(v["activity"])}</td><td>{esc(platform)}</td>'
                    f'<td class="num">{len(f["price_mentions"]) if ok else "—"}</td>'
                    f'<td class="mono">{esc(contact)}</td></tr>')
    table = (f'<div class="tablebox"><table><thead><tr><th>#</th><th>Venue</th><th>Activity</th>'
             f'<th>Booking platform</th><th>Prices</th><th>Contact</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    rule = ('<div class="rule"><strong>The rule:</strong> owner in the building beats head office. '
            'Visit off-peak, weekday 2–4pm. First four (★): Camperdown Tennis, The Cipher Room, '
            'Maze Karaoke, Kiss My Axe.</div>')
    cards = "".join(render_intel_venue(i, f) for i, f in enumerate(venues, 1))
    return table + rule + cards


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def build():
    intel = _load("reports/report_data.json")
    tracking = _load("reports/tracking_report.json")
    return render_page(intel, tracking)


def render_page(intel, tracking):
    """Render the full dashboard HTML from data dicts (no disk reads — Lambda-safe)."""
    has_tracking = bool(tracking and any(v["sold"] + v["wasted"] > 0 for v in tracking.get("venues", [])))
    default_tab = "tracking" if has_tracking else "intel"

    # summary tiles
    n_venues = len(intel["venues"]) if intel else 0
    total_sold = sum(v["sold"] for v in tracking["venues"]) if tracking else 0
    total_wasted = sum(v["wasted"] for v in tracking["venues"]) if tracking else 0
    total_open = sum(v["pending"] for v in tracking["venues"]) if tracking else 0
    total_money = tracking.get("total_wasted_money", 0) if tracking else 0
    decided = total_sold + total_wasted
    util = (total_sold / decided * 100) if decided else 0
    tracked_venues = sum(1 for v in (tracking["venues"] if tracking else []))

    tiles = [
        (str(n_venues), "Target venues", ""),
        (str(tracked_venues), "Venues tracked", ""),
        (f"{total_wasted}", "Wasted slots", "warn") if decided else (f"{total_open}", "Slots watched", ""),
        (f"{util:.0f}%", "Utilisation", "good") if decided else (str(total_sold), "Sold so far", "good"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="n {cls}">{esc(n)}</div><div class="k">{esc(k)}</div></div>'
        for n, k, cls in tiles)

    # headline lost-revenue banner
    money_banner = (
        f'<div class="moneybar"><div class="ml">Estimated revenue wasted so far</div>'
        f'<div class="mv">${total_money:,.0f}<span class="mc"> AUD</span></div>'
        f'<div class="mn">across {tracked_venues} tracked venue(s) · estimated lost revenue from '
        f'unsold slots &amp; idle bays</div></div>')

    generated = (tracking or intel or {}).get("generated_at", "")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OneTap Venue Stats</title>
<meta name="description" content="Booking intelligence and idle-inventory tracking for the OneTap founding-venue targets.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>🎯</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;700;800&family=IBM+Plex+Mono:wght@400;600;700&display=swap">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="mast">
    <div>
      <div class="brand">One<span class="t">Tap</span></div>
      <div class="eyebrow">Founding venue targets · stats</div>
      <h1>Who they book with, and what they waste.</h1>
      <p class="lede">Booking intelligence for all ten target venues, plus live idle-inventory
      tracking of the ones with an open availability feed.{(' Last updated ' + esc(generated) + '.') if generated else ''}</p>
    </div>
    <button class="themebtn" id="themebtn" type="button">◐ Theme</button>
  </header>

  {money_banner}

  <div class="tiles">{tiles_html}</div>

  <div class="tabs" role="tablist">
    <button class="tab" role="tab" id="tab-tracking" aria-controls="panel-tracking"
      aria-selected="{str(default_tab=='tracking').lower()}">Idle inventory</button>
    <button class="tab" role="tab" id="tab-intel" aria-controls="panel-intel"
      aria-selected="{str(default_tab=='intel').lower()}">Venue intelligence</button>
  </div>

  <div class="panel" id="panel-tracking" role="tabpanel" aria-labelledby="tab-tracking"
    {'hidden' if default_tab!='tracking' else ''}>{render_tracking_panel(tracking)}</div>
  <div class="panel" id="panel-intel" role="tabpanel" aria-labelledby="tab-intel"
    {'hidden' if default_tab!='intel' else ''}>{render_intel_panel(intel)}</div>

  <footer><span>OneTap · founding venue stats</span>
  <span>{('Updated ' + esc(generated)) if generated else 'Static site · GitHub Pages'}</span></footer>
</div>
<script>
(function(){{
  var tabs=[document.getElementById('tab-tracking'),document.getElementById('tab-intel')];
  var panels={{tracking:document.getElementById('panel-tracking'),intel:document.getElementById('panel-intel')}};
  function select(key){{
    tabs.forEach(function(t){{t.setAttribute('aria-selected', t.id==='tab-'+key);}});
    Object.keys(panels).forEach(function(k){{panels[k].hidden = (k!==key);}});
    try{{localStorage.setItem('onetap-tab',key);}}catch(e){{}}
  }}
  tabs.forEach(function(t){{t.addEventListener('click',function(){{select(t.id.replace('tab-',''));}});}});
  try{{var saved=localStorage.getItem('onetap-tab'); if(saved&&panels[saved]) select(saved);}}catch(e){{}}

  var btn=document.getElementById('themebtn');
  var root=document.documentElement;
  function apply(t){{ if(t) root.setAttribute('data-theme',t); }}
  try{{apply(localStorage.getItem('onetap-theme'));}}catch(e){{}}
  btn.addEventListener('click',function(){{
    var cur=root.getAttribute('data-theme');
    var dark=cur? cur==='dark' : window.matchMedia('(prefers-color-scheme:dark)').matches;
    var next=dark?'light':'dark'; apply(next);
    try{{localStorage.setItem('onetap-theme',next);}}catch(e){{}}
  }});
}})();
</script>
</body>
</html>"""


def _load(rel):
    p = HERE / rel
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


def main():
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(build())
    (DOCS / ".nojekyll").write_text("")  # serve files as-is on GitHub Pages
    print(f"Wrote {DOCS / 'index.html'}")


if __name__ == "__main__":
    main()
