"""Generate a print-ready (A4 PDF) idle-inventory + pitch report for Kiss My Axe.

Reads the real tracking + intelligence data and emits reports/KMA_REPORT.html,
which is converted to PDF with headless Chrome.
"""
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
tracking = json.loads((HERE / "reports/tracking_report.json").read_text())
intel = json.loads((HERE / "reports/report_data.json").read_text())

kma = next(v for v in tracking["venues"] if v["venue_id"] == "kiss-my-axe")
kintel = next(v for v in intel["venues"] if v["venue"]["id"] == "kiss-my-axe")
tw = tracking["tracking_window"]

# --- derived figures -------------------------------------------------------
timeline = kma["wasted_timeline"]
by_slot = defaultdict(list)
for e in timeline:
    by_slot[e["when"]].append(e)
distinct_slots = len(by_slot)
lost = kma["wasted_money"]
wasted = kma["wasted"]
sold = kma["sold"]
by_item = kma["by_item"]
dead = kma["deadest_windows"]

# conservative recovery scenario: fill 25% of distinct idle slots at their avg value
avg_slot_value = round(lost / max(1, wasted))
recover_pct = 0.25
# value of a distinct idle slot = max product value seen in that slot (avoid double count)
slot_values = [max(x["cost"] for x in items) for items in by_slot.values()]
distinct_idle_value = round(sum(slot_values))
projected = round(distinct_idle_value * recover_pct)
weekly_run_rate = round(distinct_idle_value / 2 * 7)  # window ~2 days -> /2*7

DAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
        "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}


def hour_label(h):
    ap = "am" if h < 12 else "pm"
    hh = (h - 1) % 12 + 1
    return f"{hh}{ap}"


def esc(s):
    import html
    return html.escape(str(s))


# by-item rows (max wasted for bar scaling)
mx_item = max((d["wasted"] for d in by_item.values()), default=1) or 1
item_rows = ""
for name, d in sorted(by_item.items(), key=lambda kv: kv[1]["wasted"], reverse=True):
    dec = d["wasted"] + d["sold"]
    fill = d["wasted"] / mx_item * 100
    item_rows += (
        f'<tr><td class="pname">{esc(name)}</td>'
        f'<td class="num warn">{d["wasted"]}</td>'
        f'<td class="num">{d["sold"]}</td>'
        f'<td class="barcell"><span class="bar"><i style="width:{fill:.0f}%"></i></span></td></tr>')

# deadest windows
dead_rows = "".join(
    f'<tr><td>{DAYS.get(w["day"], w["day"])}</td><td class="num">{hour_label(w["hour"])}</td>'
    f'<td class="num warn">{w["wasted"]} empty listings</td></tr>'
    for w in dead)

# empty-session log (grouped by slot, show value = max product in slot)
log_rows = ""
for when, items in list(by_slot.items())[:24]:
    names = ", ".join(sorted({i["item"].replace(" - ", " – ") for i in items}))
    val = max(i["cost"] for i in items)
    log_rows += (f'<tr><td class="mono">{esc(when)}</td><td>{esc(names)}</td>'
                 f'<td class="num money">${val:,.0f}</td></tr>')

more_note = ""
if distinct_slots > 24:
    more_note = f'<p class="fine">+ {distinct_slots - 24} more idle time-slots in the tracked window.</p>'

HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Kiss My Axe — Idle Inventory Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;600;700&display=swap">
<style>
@page {{ size: A4; margin: 14mm 0; }}
:root {{
  --ink:#17251D; --muted:#5B6B60; --deep:#0D3B2A; --deep-ink:#F2EFDF; --green:#1E8A56;
  --green-b:#2BB673; --warn:#C25B3A; --paper:#FFFFFF; --panel:#FBFAF4; --row:#F0EEE2;
  --line:#DDD8C6; --bar-bg:#E6E1CF;
}}
* {{ box-sizing:border-box; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
body {{ margin:0; color:var(--ink); background:var(--paper); font-family:'Archivo',Helvetica,Arial,sans-serif;
  font-size:10.2pt; line-height:1.5; }}
.wrap {{ padding:0 16mm; }}
h1,h2,h3 {{ margin:0; }}
.mono {{ font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace; }}
.num {{ font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }}
.warn {{ color:var(--warn); }} .money{{ color:var(--warn); font-weight:700; }}
/* cover band */
.band {{ background:var(--deep); color:var(--deep-ink); padding:12mm 16mm 10mm; }}
.brand {{ font-family:'IBM Plex Mono',monospace; font-weight:700; font-size:11pt; letter-spacing:.02em; }}
.brand .t {{ color:var(--green-b); }}
.eyebrow {{ font-family:'IBM Plex Mono',monospace; font-size:7.5pt; letter-spacing:.22em; text-transform:uppercase;
  color:var(--green-b); margin:14px 0 8px; }}
.band h1 {{ font-size:30pt; font-weight:800; letter-spacing:-.02em; line-height:1.02; }}
.band .sub {{ color:#C9D8CD; margin-top:8px; font-size:10pt; max-width:150mm; }}
.meta {{ margin-top:12px; font-family:'IBM Plex Mono',monospace; font-size:8pt; letter-spacing:.06em;
  color:#9FB6A6; text-transform:uppercase; }}
/* headline stat strip */
.stats {{ display:flex; gap:6mm; padding:8mm 16mm 4mm; }}
.stat {{ flex:1; }}
.stat .n {{ font-size:23pt; font-weight:800; line-height:1; font-variant-numeric:tabular-nums; }}
.stat .n.warn {{ color:var(--warn); }} .stat .n.good {{ color:var(--green); }}
.stat .k {{ font-family:'IBM Plex Mono',monospace; font-size:7pt; letter-spacing:.12em; text-transform:uppercase;
  color:var(--muted); margin-top:6px; }}
section {{ padding:5mm 0; break-inside:avoid; }}
.sec-h {{ font-family:'IBM Plex Mono',monospace; font-size:8pt; letter-spacing:.16em; text-transform:uppercase;
  color:var(--green); border-top:2px solid var(--deep); padding-top:6px; margin-bottom:9px; }}
h2.title {{ font-size:15pt; font-weight:800; letter-spacing:-.01em; margin-bottom:6px; }}
p {{ margin:0 0 8px; }}
.lead {{ font-size:10.5pt; }}
table {{ width:100%; border-collapse:collapse; font-size:9.4pt; }}
th {{ text-align:left; font-family:'IBM Plex Mono',monospace; font-size:7pt; letter-spacing:.1em;
  text-transform:uppercase; color:var(--muted); padding:5px 8px; border-bottom:1px solid var(--line); }}
td {{ padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:middle; }}
tr:nth-child(even) td {{ background:var(--row); }}
.pname {{ font-weight:600; }}
.barcell {{ width:34%; }}
.bar {{ display:block; height:7px; background:var(--bar-bg); position:relative; }}
.bar i {{ position:absolute; inset:0 auto 0 0; background:var(--warn); }}
.two {{ display:flex; gap:8mm; break-inside:avoid; }} .two > div {{ flex:1; }}
.callout {{ background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--green);
  padding:9px 12px; font-size:9.6pt; margin:8px 0; }}
.pillrow {{ display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }}
.pill {{ font-family:'IBM Plex Mono',monospace; font-size:7.5pt; letter-spacing:.06em; text-transform:uppercase;
  background:var(--row); border:1px solid var(--line); padding:3px 8px; }}
.offer {{ display:grid; grid-template-columns:auto 1fr; gap:5px 12px; margin-top:6px; }}
.offer .no {{ font-family:'IBM Plex Mono',monospace; color:var(--green); font-weight:700; }}
.offer .ot {{ font-size:9.8pt; }}
.offer b {{ display:block; }}
.fine {{ font-size:8pt; color:var(--muted); }}
.foot {{ border-top:1px solid var(--line); margin:6mm 16mm 0; padding:6px 0 0; font-family:'IBM Plex Mono',monospace;
  font-size:7pt; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  display:flex; justify-content:space-between; }}
.recover {{ background:var(--deep); color:var(--deep-ink); padding:8mm 12mm; margin:4mm 0; break-inside:avoid; }}
.recover .rk {{ font-family:'IBM Plex Mono',monospace; font-size:7.5pt; letter-spacing:.18em; text-transform:uppercase;
  color:var(--green-b); }}
.recover .rv {{ font-size:26pt; font-weight:800; line-height:1.05; margin-top:4px; }}
.recover .rn {{ color:#C9D8CD; font-size:9.2pt; margin-top:6px; max-width:150mm; }}
</style></head>
<body>

<div class="band">
  <div class="brand">One<span class="t">Tap</span></div>
  <div class="eyebrow">Founding venue targets · idle-inventory report · confidential</div>
  <h1>Kiss My Axe is leaving money on the floor.</h1>
  <div class="sub">A read on the sessions that ran empty at your Alexandria venue — pulled live from your
  own booking system — and what OneTap can do to fill them.</div>
  <div class="meta">Tracking window {esc(tw['first'])} → {esc(tw['last'])} · prepared for Kiss My Axe · Alexandria</div>
</div>

<div class="stats">
  <div class="stat"><div class="n warn">${lost:,.0f}</div><div class="k">Est. lost revenue*</div></div>
  <div class="stat"><div class="n warn">{distinct_slots}</div><div class="k">Idle time-slots</div></div>
  <div class="stat"><div class="n">{wasted}</div><div class="k">Empty session listings</div></div>
  <div class="stat"><div class="n good">{sold}</div><div class="k">Sessions with bookings</div></div>
</div>

<div class="wrap">

<section>
  <div class="sec-h">Executive summary</div>
  <p class="lead">Over a {esc(tw['first'][:10])} → {esc(tw['last'][:10])} window we watched the live availability of
  every Kiss My Axe Alexandria experience through your FareHarbor booking feed. In that window
  <b>{distinct_slots} distinct time-slots</b> reached their start with <b>zero bookings</b> — empty sessions on the
  floor. Valued at your own published minimum booking prices, that's an estimated
  <b>${lost:,.0f}</b> of inventory that expired unsold.*</p>
  <p>The pattern is not random. The idle time clusters in <b>weekend daytime</b>, and one product line —
  <b>Glow Darts</b> — sat empty far more than any other ({by_item.get('Glow Darts - 2-12 people',{}).get('wasted',0)}
  empty listings). These are exactly the slots a demand platform is built to fill.</p>
  <div class="callout"><b>The opportunity in one line:</b> your evenings and axe sessions sell; your
  weekend-daytime and Glow Darts slots don't. OneTap exists to put heads in those specific empty slots —
  and you only pay when we fill one that would otherwise have run empty.</div>
</section>

<section>
  <div class="sec-h">How we measured this — no guesswork</div>
  <p>This isn't a survey or an estimate of foot traffic. We polled the <b>real-time availability</b> that your
  FareHarbor system publishes, checking each session close to its start time. A slot counts as "empty" only
  when it had <b>zero customers</b> at the moment it began — a session that genuinely ran with nobody booked.
  Sessions that sold even one booking are excluded. The method is conservative: because your system hides
  exact headcounts, a half-full session is <i>not</i> counted as empty, so the real idle time is likely higher
  than what's shown here.</p>
</section>

<section>
  <div class="sec-h">What's sitting idle — by experience</div>
  <h2 class="title">Glow Darts and party packages are the softest inventory</h2>
  <table>
    <thead><tr><th>Experience</th><th class="num">Empty</th><th class="num">Booked</th><th class="barcell">Idle volume</th></tr></thead>
    <tbody>{item_rows}</tbody>
  </table>
  <p class="fine" style="margin-top:6px">Axe throwing carries most of the bookings; Glow Darts, the combo,
  and the Ultimate Party Package are where sessions repeatedly opened with no one in them.</p>
</section>

<section>
  <div class="sec-h">What's sitting idle — by time</div>
  <div class="two">
    <div>
      <h2 class="title">The dead windows</h2>
      <p>The emptiest slots concentrate on <b>Sunday during the day</b> — the hours below repeatedly reached
      start time with nothing booked. These are your prime targets for a fill campaign.</p>
      <div class="pillrow">
        <span class="pill">Weekend daytime</span><span class="pill">Glow Darts</span>
        <span class="pill">Group / party slots</span>
      </div>
    </div>
    <div>
      <table>
        <thead><tr><th>Day</th><th class="num">Hour</th><th class="num">Idle</th></tr></thead>
        <tbody>{dead_rows}</tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <div class="sec-h">The empty-session log</div>
  <h2 class="title">Every idle slot we caught</h2>
  <p class="fine">Each row is a time-slot that started with zero bookings. Value shown is the minimum booking
  value of the largest experience offered in that slot.</p>
  <table>
    <thead><tr><th>When (Sydney)</th><th>Experience(s) that ran empty</th><th class="num">Min value</th></tr></thead>
    <tbody>{log_rows}</tbody>
  </table>
  {more_note}
</section>

<div class="recover">
  <div class="rk">The recoverable prize</div>
  <div class="rv">≈ ${weekly_run_rate:,.0f} / week</div>
  <div class="rn">Extrapolating the tracked idle inventory to a full week. Recover even a quarter of it —
  a realistic target for last-minute fill — and that's about <b>${projected:,.0f}</b> of new revenue from
  slots that were going to sit empty anyway. Every dollar here is incremental: these seats earn nothing today.</div>
</div>

<section>
  <div class="sec-h">Your booking snapshot</div>
  <div class="two">
    <div>
      <table>
        <tbody>
          <tr><td class="pname">Booking platform</td><td>FareHarbor (public availability feed)</td></tr>
          <tr><td class="pname">Website</td><td>kissmyaxe.com.au</td></tr>
          <tr><td class="pname">Contact</td><td>{esc(kintel['emails'][0] if kintel['emails'] else 'hello@kissmyaxe.com.au')}</td></tr>
          <tr><td class="pname">Location</td><td>Alexandria, Sydney</td></tr>
        </tbody>
      </table>
    </div>
    <div>
      <table>
        <thead><tr><th>Experience</th><th>Published from-price</th></tr></thead>
        <tbody>
          <tr><td>Axe Throwing</td><td>from $45 pp (min 2)</td></tr>
          <tr><td>Glow Darts</td><td>from $15 pp</td></tr>
          <tr><td>Date Night / Two-for-Tuesday</td><td>from $39 / couple</td></tr>
          <tr><td>Ultimate Party Package</td><td>from $109 pp</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <div class="sec-h">What OneTap brings to Kiss My Axe</div>
  <h2 class="title">We fill the empty slots — you carry no risk</h2>
  <div class="offer">
    <div class="no">01</div><div class="ot"><b>Last-minute demand into your dead windows</b>
      We surface your unsold weekend-daytime and Glow Darts slots to nearby last-minute bookers, so the seats
      that expire empty today get filled.</div>
    <div class="no">02</div><div class="ot"><b>Smart last-minute pricing — you set the floor</b>
      A gentle discount on a slot that's about to run empty beats $0. You keep full price on your in-demand
      evenings; we only flex the inventory you tell us is soft.</div>
    <div class="no">03</div><div class="ot"><b>A live idle-inventory dashboard</b>
      The exact tracking in this report, running 24/7 for your venue — so you can see your fill rate and the
      windows that need attention, in real time.</div>
    <div class="no">04</div><div class="ot"><b>Pay only on filled slots</b>
      No subscription, no lock-in. You pay a small share only when OneTap books a slot that would otherwise
      have run empty. Everything else stays exactly as it is.</div>
  </div>
  <div class="callout" style="margin-top:10px"><b>The ask:</b> a 20-minute walkthrough where we turn on the live
  dashboard for your Alexandria venue and agree which windows to open to last-minute demand. First fill within
  the week.</div>
</section>

</div>

<div class="foot">
  <span>OneTap · Confidential · prepared for Kiss My Axe</span>
  <span>Generated {esc(tracking['generated_at'])}</span>
</div>

<div class="wrap"><p class="fine" style="margin-top:8px">* Estimated lost revenue values each empty session listing at
Kiss My Axe's own published minimum booking price (from-price × minimum party size). Because axe and glow-darts
experiences can share the same floor and time, product-level figures may overlap; the {distinct_slots}
distinct idle time-slots is the de-duplicated view. Figures are an illustrative estimate over a short
tracking window ({tw['polls']} polls), not audited revenue.</p></div>

</body></html>"""

out = HERE / "reports" / "KMA_REPORT.html"
out.write_text(HTML)
print("wrote", out)
print(f"distinct_slots={distinct_slots} wasted={wasted} sold={sold} lost=${lost:,.0f} "
      f"weekly=${weekly_run_rate:,.0f} projected=${projected:,.0f}")
