"""Render report_data.json into a styled standalone HTML report (REPORT.html)."""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

CSS = """
:root {
  --paper: #F6F3E8;
  --panel: #FDFBF3;
  --row: #ECE8D9;
  --ink: #17251D;
  --muted: #5B6B60;
  --deep: #0D3B2A;
  --deep-ink: #F2EFDF;
  --green: #1E8A56;
  --green-bright: #2BB673;
  --pill-bg: #DDEFE2;
  --warn-bg: #F3E4D2;
  --warn-ink: #8A5A1E;
  --line: #D8D2BE;
}
:root:not([data-theme="light"]) { }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0F1712; --panel: #16211A; --row: #1B281F;
    --ink: #E9E6D5; --muted: #93A398; --deep: #0D3B2A; --deep-ink: #F2EFDF;
    --green: #4CC98A; --green-bright: #2BB673;
    --pill-bg: #1E3A2B; --warn-bg: #3A2C18; --warn-ink: #E0B678;
    --line: #2A3A30;
  }
}
:root[data-theme="dark"] {
  --paper: #0F1712; --panel: #16211A; --row: #1B281F;
  --ink: #E9E6D5; --muted: #93A398; --deep: #0D3B2A; --deep-ink: #F2EFDF;
  --green: #4CC98A; --green-bright: #2BB673;
  --pill-bg: #1E3A2B; --warn-bg: #3A2C18; --warn-ink: #E0B678;
  --line: #2A3A30;
}
* { box-sizing: border-box; }
body {
  background: var(--paper); color: var(--ink);
  font-family: 'Archivo', 'Helvetica Neue', Arial, sans-serif;
  margin: 0; line-height: 1.55;
}
.mono { font-family: 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace; }
.wrap { max-width: 62rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
header.masthead { border-bottom: 3px solid var(--deep); padding-bottom: 1.5rem; margin-bottom: 1.5rem; }
.brand { font-family: 'IBM Plex Mono', monospace; font-weight: 700; font-size: .95rem; letter-spacing: .02em; }
.brand .tap { color: var(--green); }
.eyebrow {
  font-family: 'IBM Plex Mono', monospace; font-size: .68rem; font-weight: 600;
  letter-spacing: .18em; text-transform: uppercase; color: var(--green); margin: .5rem 0 .75rem;
}
h1 { font-size: clamp(1.8rem, 4.5vw, 2.6rem); font-weight: 800; letter-spacing: -.02em;
     margin: 0 0 .5rem; text-wrap: balance; }
.lede { color: var(--muted); max-width: 46rem; margin: 0; }
.tablebox { overflow-x: auto; border: 1px solid var(--line); margin: 2rem 0 1rem; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; min-width: 46rem; }
thead th {
  background: var(--deep); color: var(--deep-ink); text-align: left;
  font-family: 'IBM Plex Mono', monospace; font-size: .64rem; letter-spacing: .14em;
  text-transform: uppercase; font-weight: 600; padding: .6rem .75rem; white-space: nowrap;
}
tbody td { padding: .55rem .75rem; border-top: 1px solid var(--line); vertical-align: top; }
tbody tr:nth-child(even) { background: var(--row); }
td.num { font-family: 'IBM Plex Mono', monospace; color: var(--green); font-weight: 600; }
td .vname { font-weight: 700; }
.rule-banner {
  background: var(--green-bright); color: #0B2417; padding: .8rem 1rem; font-size: .85rem;
  font-weight: 500; margin: 1rem 0 2.5rem;
}
.rule-banner strong { font-weight: 800; }
section.venue { border: 1px solid var(--line); background: var(--panel); margin-bottom: 1.75rem; }
.vhead { display: flex; align-items: baseline; gap: .9rem; flex-wrap: wrap;
         padding: 1.1rem 1.25rem .4rem; }
.vnum { font-family: 'IBM Plex Mono', monospace; color: var(--green); font-weight: 700; font-size: 1.05rem; }
.vhead h2 { font-size: 1.35rem; font-weight: 800; letter-spacing: -.01em; margin: 0; }
.suburb { color: var(--muted); font-size: .9rem; }
.pill { font-family: 'IBM Plex Mono', monospace; font-size: .66rem; font-weight: 600;
        letter-spacing: .08em; text-transform: uppercase; padding: .25rem .6rem;
        background: var(--pill-bg); color: var(--green); white-space: nowrap; }
.pill.warn { background: var(--warn-bg); color: var(--warn-ink); }
.pill.star { background: var(--deep); color: var(--deep-ink); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
         gap: .6rem 1.5rem; padding: .75rem 1.25rem 1rem; }
.fact .k { font-family: 'IBM Plex Mono', monospace; font-size: .62rem; letter-spacing: .14em;
           text-transform: uppercase; color: var(--muted); }
.fact .v { font-size: .9rem; }
.fact .v a { color: var(--green); }
.vbody { padding: 0 1.25rem 1.25rem; display: grid; gap: 1.1rem; }
.block h3 { font-family: 'IBM Plex Mono', monospace; font-size: .68rem; letter-spacing: .16em;
            text-transform: uppercase; color: var(--green); margin: 0 0 .45rem;
            border-top: 1px solid var(--line); padding-top: .8rem; }
.block p { margin: 0; font-size: .9rem; }
ul.data { list-style: none; margin: 0; padding: 0; display: grid; gap: .3rem; }
ul.data li { font-family: 'IBM Plex Mono', monospace; font-size: .78rem; padding-left: 1rem;
             position: relative; }
ul.data li::before { content: "·"; position: absolute; left: 0; color: var(--green); font-weight: 700; }
.chips { display: flex; flex-wrap: wrap; gap: .4rem; }
.chip { font-size: .78rem; padding: .25rem .6rem; border: 1px solid var(--line);
        background: var(--row); }
.note { background: var(--warn-bg); color: var(--warn-ink); padding: .7rem .9rem; font-size: .85rem; }
details { font-size: .8rem; }
details summary { cursor: pointer; font-family: 'IBM Plex Mono', monospace; font-size: .68rem;
                  letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
details ul { margin: .5rem 0 0; padding-left: 1.1rem; }
details a { color: var(--green); word-break: break-all; }
footer { margin-top: 3rem; border-top: 1px solid var(--line); padding-top: 1rem;
         display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
         font-family: 'IBM Plex Mono', monospace; font-size: .68rem; letter-spacing: .1em;
         text-transform: uppercase; color: var(--muted); }
a { color: inherit; }
@media (prefers-reduced-motion: no-preference) {
  section.venue { scroll-margin-top: 1rem; }
}
"""


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def block(title: str, inner: str) -> str:
    return f'<div class="block"><h3>{esc(title)}</h3>{inner}</div>'


def render_venue(i: int, f: dict) -> str:
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

    facts = [
        ("Activity", esc(v["activity"])),
        ("What gets booked", esc(v["what_gets_booked"])),
        ("Walk group", esc(v["walk"])),
        ("Website", f'<a href="{esc(f.get("final_url") or v["url"])}" rel="noopener">'
                    f'{esc((f.get("final_url") or v["url"]).replace("https://", "").rstrip("/"))}</a>'),
    ]
    if f["phones"]:
        facts.append(("Phone", esc(" / ".join(f["phones"][:2]))))
    if f["emails"]:
        facts.append(("Email", esc(" / ".join(f["emails"][:2]))))
    facts_html = "".join(
        f'<div class="fact"><div class="k">{k}</div><div class="v">{v_}</div></div>'
        for k, v_ in facts)

    blocks = []
    if v.get("notes"):
        blocks.append(f'<div class="note"><strong>Research notes:</strong> {esc(v["notes"])}</div>')

    if ok:
        if f["booking_platforms"]:
            stack = (f'<p>Third-party booking platform detected: '
                     f'<strong>{esc(", ".join(f["booking_platforms"]))}</strong>.</p>')
        else:
            stack = ('<p>No known third-party booking platform detected — bookings may be '
                     'phone/email based or a custom system. <strong>Good sign for a OneTap '
                     'pitch.</strong></p>')
        if f["booking_links"]:
            links = "".join(
                f'<li><a href="{esc(bl["url"])}" rel="noopener">{esc(bl["label"])}</a></li>'
                for bl in f["booking_links"][:6])
            stack += f'<ul class="data">{links}</ul>'
        blocks.append(block("Current booking stack", stack))

        if f["offerings"]:
            chips = "".join(f'<span class="chip">{esc(o)}</span>' for o in f["offerings"])
            blocks.append(block("Bookable offerings on site", f'<div class="chips">{chips}</div>'))

        if f["price_mentions"]:
            items = "".join(f"<li>{esc(p)}</li>" for p in f["price_mentions"])
            blocks.append(block(f'Pricing signals ({len(f["price_mentions"])})',
                                f'<ul class="data">{items}</ul>'))

        if f["hours_mentions"]:
            items = "".join(f"<li>{esc(h)}</li>" for h in f["hours_mentions"])
            blocks.append(block("Opening hours signals", f'<ul class="data">{items}</ul>'))

        pages = "".join(f'<li><a href="{esc(p)}" rel="noopener">{esc(p)}</a></li>'
                        for p in f["pages_crawled"])
        blocks.append(f'<details><summary>{len(f["pages_crawled"])} pages crawled</summary>'
                      f'<ul>{pages}</ul></details>')
    else:
        blocks.append(block("Crawl status",
                            f'<p>Site unreachable during this run ({esc(f.get("error") or "unknown error")}). '
                            f'Verify manually before the visit.</p>'))

    return f"""
<section class="venue" id="{esc(v["id"])}">
  <div class="vhead">
    <span class="vnum">{i:02d}</span>
    <h2>{esc(v["name"])}</h2>
    <span class="suburb">{esc(v["suburb"])}</span>
    {"".join(pills)}
  </div>
  <div class="facts">{facts_html}</div>
  <div class="vbody">{"".join(blocks)}</div>
</section>"""


def render(data: dict) -> str:
    findings = data["venues"]
    rows = []
    for i, f in enumerate(findings, 1):
        v = f["venue"]
        ok = f["status"] == "ok"
        platform = ", ".join(f["booking_platforms"]) or ("—" if ok else "unreachable")
        contact = (f["phones"][0] if f["phones"] else f["emails"][0] if f["emails"] else "—")
        star = " ★" if v.get("first_visit") else ""
        rows.append(
            f'<tr><td class="num">{i:02d}</td>'
            f'<td><a href="#{esc(v["id"])}"><span class="vname">{esc(v["name"])}{star}</span></a><br>'
            f'<span class="suburb">{esc(v["suburb"])}</span></td>'
            f'<td>{esc(v["activity"])}</td>'
            f'<td>{esc(platform)}</td>'
            f'<td>{len(f["price_mentions"]) if ok else "—"}</td>'
            f'<td class="mono">{esc(contact)}</td></tr>')

    venues_html = "".join(render_venue(i, f) for i, f in enumerate(findings, 1))
    ok_count = sum(1 for f in findings if f["status"] == "ok")

    return f"""<title>OneTap Venue Intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;700;800&family=IBM+Plex+Mono:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header class="masthead">
    <div class="brand">One<span class="tap">Tap</span></div>
    <div class="eyebrow">Founding venue targets · LOI shortlist · booking intelligence</div>
    <h1>Ten small venues that sell by the hour.</h1>
    <p class="lede">Automated crawl of each target's public website: the booking platform they
    run today, every bookable offering, pricing and opening-hours signals, and who to call.
    Generated {esc(data["generated_at"])} · {ok_count}/{len(findings)} sites reachable.</p>
  </header>

  <div class="tablebox">
    <table>
      <thead><tr><th>#</th><th>Venue</th><th>Activity</th><th>Booking platform</th>
      <th>Prices found</th><th>Contact</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>

  <div class="rule-banner"><strong>The rule:</strong> owner in the building beats head office,
  every time. Visit off-peak, weekday 2 to 4pm. First four visits (★): Camperdown Tennis,
  The Cipher Room, Maze Karaoke, Kiss My Axe — one per category.</div>

  {venues_html}

  <footer>
    <span>OneTap · founding venue targets</span>
    <span>Confidential · generated {esc(data["generated_at"])}</span>
  </footer>
</div>"""


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "reports/report_data.json")
    out = src.parent / "REPORT.html"
    out.write_text(render(json.loads(src.read_text())))
    print(out)
