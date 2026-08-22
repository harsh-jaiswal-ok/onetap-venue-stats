"""Render tracking_report.json into a styled standalone HTML report."""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

CSS = """
:root {
  --paper:#F6F3E8; --panel:#FDFBF3; --row:#ECE8D9; --ink:#17251D; --muted:#5B6B60;
  --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#1E8A56; --green-bright:#2BB673;
  --pill-bg:#DDEFE2; --warn:#C25B3A; --warn-bg:#F3E1D9; --line:#D8D2BE; --bar-bg:#E4DFCD;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --paper:#0F1712; --panel:#16211A; --row:#1B281F; --ink:#E9E6D5; --muted:#93A398;
  --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#4CC98A; --green-bright:#2BB673;
  --pill-bg:#1E3A2B; --warn:#E08763; --warn-bg:#3A2418; --line:#2A3A30; --bar-bg:#22301F; }}
:root[data-theme="dark"]{
  --paper:#0F1712; --panel:#16211A; --row:#1B281F; --ink:#E9E6D5; --muted:#93A398;
  --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#4CC98A; --green-bright:#2BB673;
  --pill-bg:#1E3A2B; --warn:#E08763; --warn-bg:#3A2418; --line:#2A3A30; --bar-bg:#22301F; }
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);margin:0;line-height:1.55;
 font-family:'Archivo','Helvetica Neue',Arial,sans-serif}
.mono{font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace}
.wrap{max-width:60rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
header.mast{border-bottom:3px solid var(--deep);padding-bottom:1.5rem;margin-bottom:1.75rem}
.brand{font-family:'IBM Plex Mono',monospace;font-weight:700}.brand .t{color:var(--green)}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:.68rem;font-weight:600;letter-spacing:.18em;
 text-transform:uppercase;color:var(--green);margin:.5rem 0 .75rem}
h1{font-size:clamp(1.8rem,4.5vw,2.5rem);font-weight:800;letter-spacing:-.02em;margin:0 0 .5rem;text-wrap:balance}
.lede{color:var(--muted);max-width:44rem;margin:0}
.defn{background:var(--pill-bg);color:var(--ink);padding:.7rem 1rem;font-size:.85rem;margin:1.25rem 0}
.defn b{color:var(--green)}
.tablebox{overflow-x:auto;border:1px solid var(--line);margin:1.5rem 0}
table{border-collapse:collapse;width:100%;font-size:.85rem;min-width:38rem}
thead th{background:var(--deep);color:var(--deep-ink);text-align:left;font-family:'IBM Plex Mono',monospace;
 font-size:.64rem;letter-spacing:.12em;text-transform:uppercase;padding:.6rem .75rem;white-space:nowrap}
tbody td{padding:.6rem .75rem;border-top:1px solid var(--line);vertical-align:middle}
tbody tr:nth-child(even){background:var(--row)}
.num{font-variant-numeric:tabular-nums}
.util-cell{display:flex;align-items:center;gap:.5rem;min-width:9rem}
.util-track{flex:1;height:7px;background:var(--bar-bg);position:relative;overflow:hidden}
.util-fill{position:absolute;left:0;top:0;bottom:0;background:var(--green)}
section.venue{border:1px solid var(--line);background:var(--panel);margin-bottom:1.5rem;padding:1.25rem}
section.venue h2{margin:0 0 .25rem;font-size:1.3rem;font-weight:800}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:1rem}
.statrow{display:flex;flex-wrap:wrap;gap:1.5rem;margin:.5rem 0 1.25rem}
.stat .n{font-size:1.6rem;font-weight:800;font-variant-numeric:tabular-nums}
.stat .n.warn{color:var(--warn)}.stat .n.good{color:var(--green)}
.stat .k{font-family:'IBM Plex Mono',monospace;font-size:.6rem;letter-spacing:.12em;
 text-transform:uppercase;color:var(--muted)}
.block h3{font-family:'IBM Plex Mono',monospace;font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;
 color:var(--green);margin:1.1rem 0 .6rem;border-top:1px solid var(--line);padding-top:.8rem}
.heat{overflow-x:auto}
.heat table{min-width:34rem;font-size:.7rem}
.heat th,.heat td{border:1px solid var(--line);padding:.28rem;text-align:center;font-variant-numeric:tabular-nums}
.heat th{background:transparent;color:var(--muted);font-family:'IBM Plex Mono',monospace;font-weight:600;
 letter-spacing:normal;text-transform:none}
.itemgrid{display:grid;gap:.4rem}
.item{display:grid;grid-template-columns:1fr auto;gap:.5rem;align-items:center;font-size:.85rem}
.item .bar{grid-column:1/-1;height:6px;background:var(--bar-bg);position:relative;overflow:hidden}
.item .bar i{position:absolute;left:0;top:0;bottom:0;background:var(--green)}
.empty{color:var(--muted);font-size:.9rem}
footer{margin-top:2.5rem;border-top:1px solid var(--line);padding-top:1rem;
 font-family:'IBM Plex Mono',monospace;font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
 display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def heatcolor(n, mx):
    if n == 0 or mx == 0:
        return "transparent"
    t = 0.15 + 0.85 * (n / mx)
    return f"color-mix(in srgb, var(--warn) {int(t*100)}%, transparent)"


def render_heatmap(windows):
    """windows: list of {day, hour, wasted}. Build a compact weekday x hour grid."""
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
            f'<td style="background:{heatcolor(by.get((d,h),0), mx)}">{by.get((d,h),"") or ""}</td>'
            for h in hours)
        rows.append(f"<tr><th>{d}</th>{cells}</tr>")
    return (f'<div class="heat"><table><thead><tr><th></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def render_venue(v):
    decided = v["sold"] + v["wasted"]
    util = v["utilisation_pct"]
    if decided == 0:
        body = (f'<p class="empty">No slots have finished within the tracking window yet — '
                f'{v["pending"]} future slots are being watched'
                + (f', {v["unobserved"]} began before tracking started' if v["unobserved"] else '')
                + '. Leave the tracker running and re-run the report.</p>')
        return f'<section class="venue"><h2>{esc(v["venue_name"])}</h2>{body}</section>'

    stats = f"""
      <div class="stat"><div class="n warn num">{v['wasted']}</div><div class="k">Wasted slots</div></div>
      <div class="stat"><div class="n good num">{v['sold']}</div><div class="k">Sold slots</div></div>
      <div class="stat"><div class="n num">{util:.0f}%</div><div class="k">Utilisation</div></div>
      <div class="stat"><div class="n num">{v['wasted_hours']:.0f}</div><div class="k">Wasted hours</div></div>
      <div class="stat"><div class="n num">{v['pending']}</div><div class="k">Open (future)</div></div>"""

    heat = render_heatmap(v["deadest_windows"])
    heat_block = f'<div class="block"><h3>Deadest windows · wasted slots by day &amp; hour</h3>{heat}</div>' if heat else ""

    items = v["by_item"]
    if items:
        mx = max((d["wasted"] for d in items.values()), default=1) or 1
        rows = []
        for name, d in sorted(items.items(), key=lambda kv: kv[1]["wasted"], reverse=True):
            dec = d["wasted"] + d["sold"]
            iu = (d["sold"] / dec * 100) if dec else 0
            rows.append(
                f'<div class="item"><span>{esc(name)}</span>'
                f'<span class="mono num">{d["wasted"]} wasted · {d["sold"]} sold · {iu:.0f}%</span>'
                f'<span class="bar"><i style="width:{(d["wasted"]/mx*100):.0f}%"></i></span></div>')
        items_block = f'<div class="block"><h3>By offering</h3><div class="itemgrid">{"".join(rows)}</div></div>'
    else:
        items_block = ""

    return f"""<section class="venue">
      <h2>{esc(v['venue_name'])}</h2>
      <div class="sub">{decided} decided slots tracked</div>
      <div class="statrow">{stats}</div>
      {heat_block}
      {items_block}
    </section>"""


def render(data):
    venues = data["venues"]
    tw = data["tracking_window"]
    total_wasted = sum(v["wasted"] for v in venues)
    total_sold = sum(v["sold"] for v in venues)

    rows = []
    for v in venues:
        util = v["utilisation_pct"]
        rows.append(
            f'<tr><td><b>{esc(v["venue_name"])}</b></td>'
            f'<td class="num">{v["sold"]}</td><td class="num">{v["wasted"]}</td>'
            f'<td><div class="util-cell"><span class="num mono">{util:.0f}%</span>'
            f'<span class="util-track"><span class="util-fill" style="width:{util:.0f}%"></span></span></div></td>'
            f'<td class="num">{v["wasted_hours"]:.0f}</td>'
            f'<td class="num">{v["pending"]}</td></tr>')

    window = ""
    if tw.get("first"):
        window = (f'Tracking window {esc(tw["first"])} → {esc(tw["last"])} · '
                  f'{tw.get("polls",0)} polls · {tw.get("errors",0)} errors.')

    return f"""<title>Wasted-Slot Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;700;800&family=IBM+Plex+Mono:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header class="mast">
    <div class="brand">One<span class="t">Tap</span></div>
    <div class="eyebrow">Founding venue targets · idle-inventory tracking</div>
    <h1>The slots they never sold.</h1>
    <p class="lede">Live availability polled across the tracking window; every slot whose start
    time has passed is scored sold or wasted. {esc(window)} Generated {esc(data['generated_at'])}.</p>
  </header>

  <div class="defn"><b>Wasted</b> = the last check before a slot's start time still showed it open —
  bookable inventory that expired unsold. <b>Utilisation</b> = sold ÷ (sold + wasted).</div>

  <div class="tablebox"><table>
    <thead><tr><th>Venue</th><th>Sold</th><th>Wasted</th><th>Utilisation</th>
    <th>Wasted hrs</th><th>Open (future)</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>

  {"".join(render_venue(v) for v in venues)}

  <footer><span>OneTap · idle-inventory tracking</span>
  <span>{total_sold} sold · {total_wasted} wasted · generated {esc(data['generated_at'])}</span></footer>
</div>"""


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "reports/tracking_report.json")
    out = src.parent / "TRACKING_REPORT.html"
    out.write_text(render(json.loads(src.read_text())))
    print(out)
