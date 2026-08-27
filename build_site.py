"""Build the static OneTap dashboard (docs/index.html).

Data in:
  reports/tracking_report.json  -> wasted-slot stats from report.py

The page is an editorial-style waste report: hero total, per-venue day/hour
calendars (small multiples + big per-venue), a ledger table, and the raw log
of every empty hour. All data is baked into a JSON payload in the page, so it
works locally, on GitHub Pages, and from the Lambda (render_page is
disk-read-free).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent

# The calendar grids show at most this many of the most recent tracked days;
# totals and the ledger still cover the whole tracking window.
GRID_DAYS_CAP = 7

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_WHEN_RE = re.compile(r"^(\w{3}) (\d{1,2}) (\w{3}), (\d{1,2}):(\d{2})(am|pm)$")


def _parse_when(s):
    """'Sat 22 Aug, 6:00pm' (report.fmt_when) -> parts, or None."""
    m = _WHEN_RE.match(s or "")
    if not m:
        return None
    dow, day, mon, h12, minute, ampm = m.groups()
    hour = int(h12) % 12 + (12 if ampm == "pm" else 0)
    return {"key": (_MONTHS.get(mon, 0), int(day)), "hour": hour,
            "time": f"{int(h12)}:{minute}{ampm}"}


def _parse_stamp(s):
    """'2026-08-22 19:21 AEST' -> datetime, or None."""
    try:
        return datetime.strptime((s or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _fmt12(dt):
    return f"{(dt.hour - 1) % 12 + 1}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def _day_axis(tracking):
    """Most recent GRID_DAYS_CAP days of the tracking window."""
    tw = (tracking or {}).get("tracking_window") or {}
    first, last = _parse_stamp(tw.get("first")), _parse_stamp(tw.get("last"))
    if not first or not last:
        return [], [], {}
    dates = []
    d = first.date()
    while d <= last.date():
        dates.append(d)
        d += timedelta(days=1)
    dates = dates[-GRID_DAYS_CAP:]
    labels = [f"{d:%a} {d.day}" for d in dates]
    full = [f"{d:%a} {d.day} {d:%b}" for d in dates]
    index = {(d.month, d.day): i for i, d in enumerate(dates)}
    return labels, full, index


def _venue_payload(v, day_index, n_days, hours):
    cap = v.get("kind") == "capacity"
    hour_index = {h: i for i, h in enumerate(hours)}
    grid = [[0] * len(hours) for _ in range(n_days)]
    gmoney = [[0] * len(hours) for _ in range(n_days)]
    log = []
    for e in v.get("wasted_timeline") or []:
        p = _parse_when(e.get("when", ""))
        if not p:
            continue
        d = day_index.get(p["key"])
        if d is None:
            continue  # outside the capped grid window
        n = int(e.get("idle") or 0) if cap else 1
        amount = int(round(e.get("cost") or 0))
        h = hour_index.get(p["hour"])
        if h is not None:
            grid[d][h] += n
            gmoney[d][h] += amount
        log.append([d, p["time"], str(n) if cap else (e.get("item") or ""), amount])

    out = {
        "name": v.get("venue_name", ""),
        "kind": "capacity" if cap else "slots",
        "lost": int(round(v.get("wasted_money") or 0)),
        "grid": grid,
        "gridMoney": gmoney,
        "gridMax": max((c for row in grid for c in row), default=0) or 1,
        "log": log,
    }
    if cap:
        out.update(occ=round(v.get("utilisation_pct") or 0),
                   idle=round(v.get("idle_unit_hours") or 0),
                   booked=round(v.get("busy_unit_hours") or 0),
                   ahead=v.get("pending") or 0,
                   bays=v.get("total_units") or 0,
                   offerings=[])
    else:
        wasted, sold = v.get("wasted") or 0, v.get("sold") or 0
        offerings = sorted(
            ({"name": k, "wasted": i.get("wasted") or 0, "sold": i.get("sold") or 0}
             for k, i in (v.get("by_item") or {}).items()),
            key=lambda o: o["wasted"], reverse=True)
        out.update(util=round(v.get("utilisation_pct") or 0),
                   wasted=wasted, sold=sold,
                   future=v.get("pending") or 0,
                   decided=wasted + sold,
                   offerings=offerings)
    return out


def _payload(tracking):
    tracking = tracking or {}
    venues = tracking.get("venues") or []
    tw = tracking.get("tracking_window") or {}
    days, days_full, day_index = _day_axis(tracking)

    parsed = [p for v in venues for e in (v.get("wasted_timeline") or [])
              if (p := _parse_when(e.get("when", ""))) and p["key"] in day_index]
    hour_vals = [p["hour"] for p in parsed]
    hours = list(range(min(hour_vals), max(hour_vals) + 1)) if hour_vals else list(range(7, 22))

    total_sold = sum(v.get("sold") or 0 for v in venues)
    total_wasted = sum(v.get("wasted") or 0 for v in venues)
    decided = total_sold + total_wasted
    first, last = _parse_stamp(tw.get("first")), _parse_stamp(tw.get("last"))
    gen = _parse_stamp(tracking.get("generated_at"))

    return {
        "totalLost": int(round(tracking.get("total_wasted_money") or 0)),
        "venueCount": len(venues),
        "wastedSlots": total_wasted,
        "utilisation": round(100 * total_sold / decided) if decided else 0,
        "windowStart": f"{first.day} {first:%b}, {_fmt12(first)}" if first else "",
        "windowEnd": f"{last.day} {last:%b}, {_fmt12(last)}" if last else "",
        "polls": tw.get("polls") or 0,
        "errors": tw.get("errors") or 0,
        "updated": (f"{gen.day} {gen:%b} {gen:%Y}, {_fmt12(gen)} AEST"
                    if gen else (tracking.get("generated_at") or "")),
        "days": days,
        "daysFull": days_full,
        "hours": hours,
        "venues": [_venue_payload(v, day_index, len(days), hours) for v in venues],
    }


TEMPLATE = """<!doctype html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OneTap — venue waste report</title>
<meta name="description" content="Unsold slots and idle bays across Sydney venues, read straight off their booking feeds.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>🎯</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#E6E8EA;
  --panel:#F2F3F4;
  --ink:#15181C;
  --ink-2:#59616A;
  --ink-3:#8E969E;
  --rule:#C4CACE;
  --rule-soft:#D6DADD;
  --mark:#E2FB3C;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:"Archivo",system-ui,-apple-system,Segoe UI,sans-serif;
  --pad:clamp(20px,5vw,64px);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font-family:var(--sans);font-size:16px;line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px;margin:0 auto;padding:0 var(--pad)}

/* ---------- masthead ---------- */
.masthead{
  position:sticky;top:0;z-index:40;background:var(--bg);
  border-bottom:1px solid var(--ink);
}
.masthead .wrap{
  display:flex;align-items:baseline;justify-content:space-between;
  gap:16px;padding-top:12px;padding-bottom:11px;
}
.wordmark{font-weight:700;letter-spacing:-.03em;font-size:17px}
.stamp{
  font-family:var(--mono);font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-2);text-align:right;
}
.stamp b{font-weight:500;color:var(--ink)}

/* ---------- hero ---------- */
.hero{padding-top:clamp(48px,9vw,104px);padding-bottom:clamp(40px,7vw,72px)}
.eyebrow{
  font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink-2);margin:0 0 clamp(20px,3vw,32px)
}
.figure{
  font-family:var(--mono);font-weight:600;
  font-size:clamp(62px,15.5vw,188px);line-height:.86;letter-spacing:-.045em;
  margin:0;position:relative;display:inline-block;
}
.lede{
  font-size:clamp(18px,2.1vw,25px);line-height:1.32;letter-spacing:-.018em;
  max-width:22ch;margin:clamp(24px,3.5vw,38px) 0 0;font-weight:500;
}
.lede em{font-style:normal;color:var(--ink-2)}
.provenance{
  font-family:var(--mono);font-size:11.5px;line-height:1.75;color:var(--ink-2);
  margin:clamp(28px,4vw,44px) 0 0;padding-top:14px;
  border-top:1px solid var(--rule);max-width:56ch;
}

/* ---------- section furniture ---------- */
.section{padding-top:clamp(44px,6vw,80px)}
.rule-top{border-top:1px solid var(--ink)}
.shead{display:flex;flex-wrap:wrap;gap:8px 28px;align-items:baseline;margin-bottom:clamp(24px,3vw,36px)}
.shead h2{
  font-size:clamp(21px,2.4vw,30px);letter-spacing:-.03em;font-weight:600;margin:0;
}
.shead p{font-size:14.5px;color:var(--ink-2);margin:0;max-width:52ch;line-height:1.45}

/* ---------- small multiples ---------- */
.multiples{
  display:grid;gap:clamp(20px,2.6vw,34px);
  grid-template-columns:repeat(auto-fit,minmax(228px,1fr));
}
.mpanel{min-width:0}
.mpanel h3{
  font-size:14px;font-weight:600;letter-spacing:-.01em;margin:0 0 2px;
}
.mpanel .mmeta{
  font-family:var(--mono);font-size:11px;color:var(--ink-2);margin:0 0 12px;
}
.cal{display:grid;grid-template-columns:22px 1fr;gap:0 6px;align-items:stretch}
.cal-days{display:grid;gap:2px;grid-auto-rows:1fr}
.cal-day{
  font-family:var(--mono);font-size:9.5px;color:var(--ink-3);line-height:1;
  display:flex;align-items:center;justify-content:flex-end;
}
.cal-rows{display:grid;gap:2px;min-width:0}
.cal-row{display:grid;gap:2px;min-width:0}
.cell{
  aspect-ratio:1;border:1px solid var(--rule-soft);background:transparent;
  position:relative;min-width:0;
}
.cell[data-v]{border-color:transparent}
.cell.peak{
  outline:2px solid var(--mark);outline-offset:0;
  border-color:transparent;z-index:2;
}
.cal-hours{
  grid-column:2;display:flex;justify-content:space-between;
  font-family:var(--mono);font-size:9.5px;color:var(--ink-3);margin-top:6px;
}
.legend{
  display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  font-family:var(--mono);font-size:11px;color:var(--ink-2);
  margin-top:clamp(22px,3vw,32px);padding-top:12px;border-top:1px solid var(--rule);
}
.ramp{display:flex;gap:2px}
.ramp i{width:13px;height:13px;display:block;border:1px solid var(--rule-soft)}
.ramp i.peak-swatch{outline:2px solid var(--mark);outline-offset:-2px;border-color:transparent}

/* ---------- ledger table ---------- */
.ledger{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:13px}
.ledger th{
  text-align:left;font-weight:500;font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-2);
  padding:0 12px 10px 0;border-bottom:1px solid var(--ink);white-space:nowrap;
}
.ledger td{padding:13px 12px 13px 0;border-bottom:1px solid var(--rule-soft);vertical-align:baseline}
.ledger tr:last-child td{border-bottom:1px solid var(--ink)}
.ledger .num{text-align:right;font-variant-numeric:tabular-nums}
.ledger .vname{font-family:var(--sans);font-weight:600;font-size:15px;letter-spacing:-.015em}
.ledger a{color:inherit;text-decoration:none;border-bottom:1px solid var(--rule)}
.ledger a:hover{border-bottom-color:var(--ink)}
.ledger .lost{font-weight:600}
.bar{position:relative;height:7px;background:var(--rule-soft);min-width:64px;margin-top:5px;display:block}
.bar i{position:absolute;inset:0 auto 0 0;background:var(--ink);display:block}
.note{
  font-family:var(--mono);font-size:11.5px;line-height:1.7;color:var(--ink-2);
  margin:18px 0 0;max-width:74ch;
}
.note b{color:var(--ink);font-weight:500}

/* ---------- venue detail ---------- */
.venue{padding-top:clamp(52px,7vw,92px)}
.vhead{
  display:flex;flex-wrap:wrap;gap:6px 24px;align-items:flex-end;
  justify-content:space-between;
  border-bottom:1px solid var(--ink);padding-bottom:14px;margin-bottom:clamp(24px,3vw,34px);
}
.vhead h2{font-size:clamp(24px,3.4vw,42px);letter-spacing:-.035em;font-weight:600;margin:0}
.vhead .vkind{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);display:block;margin-bottom:6px}
.vlost{font-family:var(--mono);font-size:clamp(24px,3.2vw,38px);font-weight:600;letter-spacing:-.03em;white-space:nowrap}
.vgrid{display:grid;grid-template-columns:minmax(0,280px) minmax(0,1fr);gap:clamp(26px,4vw,56px)}
dl.stats{margin:0;font-family:var(--mono);font-size:13px}
dl.stats div{display:flex;justify-content:space-between;gap:16px;padding:9px 0;border-bottom:1px solid var(--rule-soft)}
dl.stats div:first-child{border-top:1px solid var(--rule-soft)}
dl.stats dt{color:var(--ink-2);font-size:11.5px;letter-spacing:.04em}
dl.stats dd{margin:0;font-weight:600;font-variant-numeric:tabular-nums}
.subhead{
  font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--ink-2);margin:clamp(26px,3vw,34px) 0 12px;font-weight:500;
}
.vgrid .subhead:first-child{margin-top:0}
.offer{margin-top:11px}
.offer-top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.offer-name{font-size:13.5px;letter-spacing:-.01em;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.offer-fig{font-family:var(--mono);font-size:11px;color:var(--ink-2);white-space:nowrap}
.offer-bar{display:flex;height:8px;margin-top:5px;background:var(--rule-soft)}
.offer-bar .w{background:var(--ink)}
.offer-bar .s{background:var(--mark)}

/* big calendar */
.bigcal .cal{grid-template-columns:30px 1fr;gap:0 8px}
.bigcal .cal-day{font-size:11px}
.bigcal .cal-hours{font-size:10px}
.bigcal .cell{border-radius:1px}

/* log */
.log{margin-top:clamp(28px,3.5vw,40px)}
.logbox{
  max-height:340px;overflow:auto;border-top:1px solid var(--ink);
  border-bottom:1px solid var(--ink);background:var(--panel);
}
.logtable{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12.5px}
.logtable td{padding:7px 14px;border-bottom:1px solid var(--rule-soft);white-space:nowrap}
.logtable tr:last-child td{border-bottom:0}
.logtable td:nth-child(2){white-space:normal;color:var(--ink-2);width:99%}
.logtable td:last-child{text-align:right;font-variant-numeric:tabular-nums;font-weight:500}
.logtable tr:hover td{background:var(--bg)}

footer{
  margin-top:clamp(56px,8vw,110px);border-top:1px solid var(--ink);
  padding:16px 0 40px;
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px 24px;
  font-family:var(--mono);font-size:11px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--ink-2);
}

/* tooltip */
#tip{
  position:fixed;z-index:80;pointer-events:none;opacity:0;
  background:var(--ink);color:var(--bg);
  font-family:var(--mono);font-size:11.5px;line-height:1.45;
  padding:7px 10px;white-space:nowrap;transform:translate(-50%,-134%);
  transition:opacity .09s linear;
}
#tip.on{opacity:1}

:focus-visible{outline:2px solid var(--ink);outline-offset:3px}

@media (max-width:880px){
  .vgrid{grid-template-columns:1fr}
  .ledger .hide-sm{display:none}
}

@keyframes riseIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
.cell{animation:riseIn .28s ease-out backwards}
@media (prefers-reduced-motion:reduce){
  .cell{animation:none}
  *{transition-duration:.001ms!important}
}
</style>
</head>
<body>

<header class="masthead">
  <div class="wrap">
    <span class="wordmark">OneTap</span>
    <span class="stamp">Venue waste report<br><b id="stamp-date"></b></span>
  </div>
</header>

<main class="wrap">

  <section class="hero">
    <p class="eyebrow">Founding-venue targets — idle inventory</p>
    <h1 class="figure"><span id="hero-total"></span></h1>
    <p class="lede">walked out the door unsold. <em>__LEDE_META__</em></p>
    <p class="provenance" id="provenance"></p>
  </section>

  <section class="section rule-top">
    <div class="shead">
      <h2>Where the empty hours sit</h2>
      <p>One block per venue. Rows are days, columns are opening hours. Darker means more inventory went unsold in that hour; the boxed cell is each venue's single worst hour of the week.</p>
    </div>
    <div class="multiples" id="multiples"></div>
    <div class="legend">
      <span>none</span>
      <span class="ramp" id="ramp"></span>
      <span>most wasted</span>
      <span class="ramp"><i class="peak-swatch" style="background:rgba(21,24,28,.95);border-color:transparent"></i></span>
      <span>worst hour of the week</span>
    </div>
  </section>

  <section class="section rule-top">
    <div class="shead">
      <h2>The ledger</h2>
    </div>
    <table class="ledger">
      <thead>
        <tr>
          <th>Venue</th>
          <th class="num">Lost revenue</th>
          <th class="hide-sm">Utilisation</th>
          <th class="num hide-sm">Wasted</th>
          <th class="num">Still open</th>
        </tr>
      </thead>
      <tbody id="ledger-body"></tbody>
    </table>
    <p class="note">
      For venues that sell fixed sessions__SLOT_VENUES__, a slot counts as <b>wasted</b> when its start time passed with zero bookings on it. For venues that sell bays or courts by the hour, we read the live booking count and total the <b>units sitting empty</b> each operating hour. Lost revenue applies each venue's own list price and takes no view on discounts.
    </p>
  </section>

  <div id="venues"></div>

</main>

<footer class="wrap">
  <span>OneTap — founding venue stats</span>
  <span id="foot-date"></span>
</footer>

<div id="tip" role="status" aria-live="polite"></div>

<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
(function(){
  var D = JSON.parse(document.getElementById('payload').textContent);
  var money = function(n){ return '$' + n.toLocaleString('en-AU'); };
  var HOURS = D.hours;

  document.getElementById('stamp-date').textContent = D.updated;
  document.getElementById('foot-date').textContent = 'Updated ' + D.updated;
  document.getElementById('hero-total').textContent = money(D.totalLost);
  document.getElementById('provenance').textContent =
    'Watching since ' + D.windowStart + '. Last read ' + D.windowEnd + '. ' +
    D.polls + ' polls, ' + D.errors + ' failed. ' +
    D.wastedSlots + ' wasted slots across ' + D.venueCount + ' venues, ' +
    D.utilisation + '% of tracked inventory sold.';

  // colour ramp
  function shade(v, max){
    if(!v) return null;
    var t = Math.pow(v / max, 0.62);
    return 'rgba(21,24,28,' + (0.13 + 0.82 * t).toFixed(3) + ')';
  }

  var ramp = document.getElementById('ramp');
  [0.12,0.3,0.5,0.72,1].forEach(function(t){
    var i = document.createElement('i');
    i.style.background = shade(t, 1);
    i.style.borderColor = 'transparent';
    ramp.appendChild(i);
  });

  var tip = document.getElementById('tip');
  function showTip(e, text){
    tip.textContent = text;
    tip.classList.add('on');
    var r = e.currentTarget.getBoundingClientRect();
    tip.style.left = (r.left + r.width/2) + 'px';
    tip.style.top = r.top + 'px';
  }
  function hideTip(){ tip.classList.remove('on'); }

  function unitFor(v){ return v.kind === 'capacity' ? 'units idle' : 'slots unsold'; }

  function buildCal(v, opts){
    opts = opts || {};
    var peak = {d:-1,h:-1,val:0};
    v.grid.forEach(function(row,d){
      row.forEach(function(val,h){ if(val > peak.val) peak = {d:d,h:h,val:val}; });
    });

    var cal = document.createElement('div');
    cal.className = 'cal';

    var days = document.createElement('div');
    days.className = 'cal-days';
    D.days.forEach(function(name){
      var el = document.createElement('div');
      el.className = 'cal-day';
      el.textContent = opts.big ? name : name.slice(0,3);
      days.appendChild(el);
    });

    var rows = document.createElement('div');
    rows.className = 'cal-rows';
    var n = 0;
    v.grid.forEach(function(row, d){
      var r = document.createElement('div');
      r.className = 'cal-row';
      r.style.gridTemplateColumns = 'repeat(' + HOURS.length + ',minmax(0,1fr))';
      row.forEach(function(val, h){
        var c = document.createElement('div');
        c.className = 'cell';
        c.style.animationDelay = (n++ * 3.5) + 'ms';
        if(val){
          c.dataset.v = val;
          c.style.background = shade(val, v.gridMax);
          if(d === peak.d && h === peak.h) c.className += ' peak';
          var hr = HOURS[h];
          var label = D.daysFull[d] + ', ' + ((hr % 12) || 12) + (hr < 12 ? 'am' : 'pm') +
                      ' — ' + val + ' ' + unitFor(v) + ', ' + money(v.gridMoney[d][h]);
          c.tabIndex = 0;
          c.setAttribute('role','img');
          c.setAttribute('aria-label', label);
          c.addEventListener('mouseenter', function(e){ showTip(e, label); });
          c.addEventListener('focus', function(e){ showTip(e, label); });
          c.addEventListener('mouseleave', hideTip);
          c.addEventListener('blur', hideTip);
        }
        r.appendChild(c);
      });
      rows.appendChild(r);
    });

    var hoursRow = document.createElement('div');
    hoursRow.className = 'cal-hours';
    [HOURS[0], HOURS[Math.floor(HOURS.length/2)], HOURS[HOURS.length-1]].forEach(function(h){
      var s = document.createElement('span');
      s.textContent = ((h % 12) || 12) + (h < 12 ? 'am' : 'pm');
      hoursRow.appendChild(s);
    });

    cal.appendChild(days);
    cal.appendChild(rows);
    cal.appendChild(hoursRow);
    return cal;
  }

  function slug(name){ return name.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,''); }

  // --- small multiples
  var mult = document.getElementById('multiples');
  D.venues.forEach(function(v){
    var p = document.createElement('section');
    p.className = 'mpanel';
    var h = document.createElement('h3');
    h.textContent = v.name;
    var m = document.createElement('p');
    m.className = 'mmeta';
    m.textContent = money(v.lost) + ' — ' +
      (v.kind === 'capacity' ? v.idle + ' idle unit-hours' : v.wasted + ' wasted slots');
    p.appendChild(h); p.appendChild(m);
    p.appendChild(buildCal(v, {}));
    mult.appendChild(p);
  });

  // --- ledger
  var tb = document.getElementById('ledger-body');
  D.venues.forEach(function(v){
    var pct = v.kind === 'capacity' ? v.occ : v.util;
    var tr = document.createElement('tr');
    tr.innerHTML =
      '<td><a class="vname" href="#' + slug(v.name) + '">' + v.name + '</a></td>' +
      '<td class="num lost">' + money(v.lost) + '</td>' +
      '<td class="hide-sm">' + pct + '% ' + (v.kind === 'capacity' ? 'occupancy' : 'utilisation') +
        '<span class="bar"><i style="width:' + pct + '%"></i></span></td>' +
      '<td class="num hide-sm">' + (v.kind === 'capacity' ? v.idle + ' unit-hrs' : v.wasted + ' slots') + '</td>' +
      '<td class="num">' + (v.kind === 'capacity' ? v.ahead + ' hrs' : v.future + ' slots') + '</td>';
    tb.appendChild(tr);
  });

  // --- venue detail
  var host = document.getElementById('venues');
  D.venues.forEach(function(v){
    var sec = document.createElement('section');
    sec.className = 'venue';
    sec.id = slug(v.name);

    var head = document.createElement('div');
    head.className = 'vhead';
    head.innerHTML =
      '<div><span class="vkind">' +
        (v.kind === 'capacity' ? v.bays + ' units, sold by the hour' : v.decided + ' sessions decided so far') +
      '</span><h2>' + v.name + '</h2></div>' +
      '<div class="vlost">' + money(v.lost) + ' lost</div>';
    sec.appendChild(head);

    var grid = document.createElement('div');
    grid.className = 'vgrid';

    // left column
    var left = document.createElement('div');
    var pairs = v.kind === 'capacity'
      ? [['Idle unit-hours', v.idle], ['Booked unit-hours', v.booked], ['Occupancy', v.occ + '%'], ['Hours ahead', v.ahead]]
      : [['Wasted slots', v.wasted], ['Sold slots', v.sold], ['Utilisation', v.util + '%'], ['Open ahead', v.future]];
    var dl = '<dl class="stats">';
    pairs.forEach(function(p){ dl += '<div><dt>' + p[0] + '</dt><dd>' + p[1] + '</dd></div>'; });
    dl += '</dl>';
    left.innerHTML = dl;

    if(v.offerings && v.offerings.length){
      var oh = document.createElement('p');
      oh.className = 'subhead';
      oh.textContent = 'By offering';
      left.appendChild(oh);
      v.offerings.forEach(function(o){
        var total = o.wasted + o.sold || 1;
        var pct = Math.round(o.sold / total * 100);
        var d = document.createElement('div');
        d.className = 'offer';
        d.innerHTML =
          '<div class="offer-top"><span class="offer-name"></span>' +
          '<span class="offer-fig">' + pct + '% sold</span></div>' +
          '<div class="offer-bar"><span class="w" style="flex:' + o.wasted + '"></span>' +
          '<span class="s" style="flex:' + o.sold + '"></span></div>';
        var nameEl = d.querySelector('.offer-name');
        nameEl.textContent = o.name;
        nameEl.title = o.name;
        left.appendChild(d);
      });
    }

    // right column
    var right = document.createElement('div');
    right.className = 'bigcal';
    var rh = document.createElement('p');
    rh.className = 'subhead';
    rh.textContent = v.kind === 'capacity' ? 'Idle units by day and hour' : 'Unsold slots by day and hour';
    right.appendChild(rh);
    right.appendChild(buildCal(v, {big:true}));

    grid.appendChild(left);
    grid.appendChild(right);
    sec.appendChild(grid);

    // log
    if(v.log.length){
      var log = document.createElement('div');
      log.className = 'log';
      var lh = document.createElement('p');
      lh.className = 'subhead';
      lh.textContent = 'Every empty hour, in order — ' + v.log.length + ' entries';
      log.appendChild(lh);
      var box = document.createElement('div');
      box.className = 'logbox';
      var table = document.createElement('table');
      table.className = 'logtable';
      var tbody = document.createElement('tbody');
      v.log.forEach(function(r){
        var tr = document.createElement('tr');
        var what = v.kind === 'capacity' ? r[2] + ' units idle' : r[2];
        [D.daysFull[r[0]] + ', ' + r[1], what, money(r[3])].forEach(function(t){
          var td = document.createElement('td');
          td.textContent = t;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      box.appendChild(table);
      log.appendChild(box);
      sec.appendChild(log);
    }

    host.appendChild(sec);
  });

  window.addEventListener('scroll', hideTip, {passive:true});
})();
</script>
</body>
</html>
"""


def render_page(intel, tracking):
    """Render the full dashboard HTML from data dicts (no disk reads — Lambda-safe).

    `intel` (venue-intelligence data) is accepted for Lambda compatibility but
    unused: the dashboard is a pure idle-inventory report.
    """
    data = _payload(tracking)
    slot_names = [v["name"] for v in data["venues"] if v["kind"] == "slots" and v["name"]]
    lede = (f"{data['venueCount']} Sydney venues over {len(data['days'])} days, "
            "read straight off their own booking feeds."
            if data["venueCount"] else
            "No tracking data yet — the first poll is on its way.")
    payload_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (TEMPLATE
            .replace("__PAYLOAD__", payload_json)
            .replace("__LEDE_META__", lede)
            .replace("__SLOT_VENUES__", f" — {', '.join(slot_names)}" if slot_names else ""))


def _load(rel):
    try:
        return json.loads((ROOT / rel).read_text())
    except (OSError, ValueError):
        return None


def build():
    intel = _load("reports/report_data.json")
    tracking = _load("reports/tracking_report.json")
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(render_page(intel, tracking))
    print(f"Wrote {out}")


if __name__ == "__main__":
    build()
