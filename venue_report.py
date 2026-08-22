"""OneTap venue intelligence report.

Crawls each target venue's public website, extracts everything related to how
they take bookings (booking platform, prices, session types, opening hours,
contact details), and writes a detailed Markdown report per venue plus a
combined summary.

Runs locally (python venue_report.py) or inside AWS Lambda (see lambda_handler.py).
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15
MAX_PAGES_PER_SITE = 8
CRAWL_DELAY_SECONDS = 0.5

# Keywords that mark a link as worth crawling (booking / pricing / offer pages).
INTERESTING_LINK_WORDS = [
    "book", "booking", "reserve", "reservation", "price", "pricing", "rates",
    "hire", "session", "package", "party", "parties", "group", "event",
    "function", "court", "room", "experience", "gift", "faq", "contact",
    "hours", "opening",
]

# Signatures of third-party booking systems, matched against page HTML,
# script/iframe/link URLs. Knowing the platform tells OneTap what the venue's
# current booking stack is before walking in.
BOOKING_PLATFORM_SIGNATURES = {
    "Rezdy": ["rezdy.com"],
    "Checkfront": ["checkfront.com"],
    "TryBooking": ["trybooking.com"],
    "Bookeo": ["bookeo.com"],
    "ROLLER": ["roller.app", "rollerdigital.com"],
    "FareHarbor": ["fareharbor.com"],
    "Resova": ["resova.com"],
    "Xola": ["xola.com"],
    "Square Appointments": ["squareup.com/appointments", "square.site"],
    "Timely": ["gettimely.com", "bookings.timely"],
    "Mindbody": ["mindbodyonline.com"],
    "Setmore": ["setmore.com"],
    "Calendly": ["calendly.com"],
    "Skedda": ["skedda.com"],
    "ClubSpark / Tennis Australia": ["clubspark", "book.tennis.com.au", "play.tennis.com.au"],
    "Bookable": ["bookable"],
    "SimplyBook": ["simplybook."],
    "Eventbrite": ["eventbrite."],
    "Fresha": ["fresha.com"],
    "OpenTable": ["opentable.com"],
    "Now Book It": ["nowbookit.com"],
    "SevenRooms": ["sevenrooms.com"],
    "Quandoo": ["quandoo."],
    "TheFork": ["thefork."],
    "Wix Bookings": ["wixbookings", "wix-bookings"],
    "Squarespace Scheduling / Acuity": ["acuityscheduling.com", "squarespacescheduling.com"],
    "Shopify": ["cdn.shopify.com"],
    "Escape Room Master": ["escaperoommaster", "bookingslive"],
    "Buk (bukk.io)": ["bukk.io"],
    "Yepbooking": ["yepbooking"],
    "Omnify": ["getomnify.com"],
    "Picktime": ["picktime.com"],
    "Intrac (court booking)": ["intrac.com.au"],
}

# Site-builder / CMS fingerprints — useful context on how sophisticated the
# venue's web presence is.
CMS_SIGNATURES = {
    "WordPress": ["wp-content", "wp-includes"],
    "Wix": ["wix.com", "wixstatic.com"],
    "Squarespace": ["squarespace.com", "squarespace-cdn"],
    "Shopify": ["cdn.shopify.com"],
    "Webflow": ["webflow"],
    "GoDaddy Website Builder": ["godaddy.com"],
}

PRICE_RE = re.compile(r"\$\s?\d{1,4}(?:\.\d{2})?(?:\s?(?:pp|/hr|per hour|per person|p/p|each))?", re.I)
PHONE_RE = re.compile(
    r"(?<![\d.])(?:"
    r"\+61\s?\d(?:[ \-]?\d){8}"          # +61 4 1234 5678
    r"|\(0\d\)\s?\d{4}[ \-]?\d{4}"       # (02) 9281 9006
    r"|0\d(?:[ \-]?\d){8}"               # 0406 336 471 / 02 9281 9006
    r"|1[38]00(?:[ \-]?\d){6}"           # 1300 / 1800 numbers
    r")(?![\d.])")


def normalise_phone(raw: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("+61") and len(digits) == 12:
        return raw.strip()
    if digits.startswith(("1300", "1800")) and len(digits) == 10:
        return raw.strip()
    if digits.startswith("0") and len(digits) == 10:
        return raw.strip()
    return None
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
DAY_WORDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "mon", "tue", "wed", "thu", "fri", "sat", "sun", "weekday", "weekend", "daily", "7 days"]
TIME_RE = re.compile(r"\d{1,2}(?::\d{2})?\s?(?:am|pm)", re.I)


@dataclass
class VenueFindings:
    venue: dict
    status: str = "ok"
    error: str | None = None
    final_url: str | None = None
    pages_crawled: list[str] = field(default_factory=list)
    booking_platforms: list[str] = field(default_factory=list)
    cms: list[str] = field(default_factory=list)
    booking_links: list[dict] = field(default_factory=list)
    price_mentions: list[str] = field(default_factory=list)
    hours_mentions: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    socials: list[str] = field(default_factory=list)
    page_titles: list[str] = field(default_factory=list)
    offerings: list[str] = field(default_factory=list)


def fetch(url: str, session: requests.Session) -> requests.Response | None:
    # Some small-venue sites serve incomplete certificate chains that browsers
    # tolerate but requests rejects — retry unverified before giving up.
    for verify in (True, False):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, verify=verify)
            if resp.status_code < 400 and "text/html" in resp.headers.get("content-type", "text/html"):
                return resp
            return None
        except requests.exceptions.SSLError:
            continue
        except requests.RequestException:
            return None
    return None


def same_site(url: str, base: str) -> bool:
    a, b = urlparse(url).netloc.lower(), urlparse(base).netloc.lower()
    return a.removeprefix("www.") == b.removeprefix("www.")


def clean_text(s: str, limit: int = 200) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def detect_signatures(html: str, signatures: dict[str, list[str]]) -> list[str]:
    html_lower = html.lower()
    return [name for name, needles in signatures.items()
            if any(n in html_lower for n in needles)]


def extract_context_snippets(soup: BeautifulSoup, pattern: re.Pattern, require_words: list[str] | None = None,
                             limit: int = 240) -> list[str]:
    """Return deduped text snippets (block-level) that match `pattern`."""
    snippets: list[str] = []
    seen: set[str] = set()
    for el in soup.find_all(["p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "div", "span"]):
        # Skip containers with lots of children — we want leaf-ish text blocks.
        if el.find_all(["p", "li", "div", "table"]):
            continue
        text = clean_text(el.get_text(" ", strip=True), limit)
        if not text or len(text) < 4:
            continue
        if not pattern.search(text):
            continue
        if require_words and not any(w in text.lower() for w in require_words):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        snippets.append(text)
    return snippets


def extract_offerings(soup: BeautifulSoup) -> list[str]:
    """Headings that look like products / session types / rooms."""
    offer_words = ["room", "court", "session", "package", "experience", "party",
                   "hire", "game", "karaoke", "axe", "escape", "vr", "soccer",
                   "tennis", "dart", "corporate", "function", "birthday"]
    found: list[str] = []
    seen: set[str] = set()
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = clean_text(h.get_text(" ", strip=True), 120)
        if not text or len(text) > 90:
            continue
        if not any(w in text.lower() for w in offer_words):
            continue
        key = text.lower()
        if key not in seen:
            seen.add(key)
            found.append(text)
    return found


def crawl_venue(venue: dict, session: requests.Session) -> VenueFindings:
    f = VenueFindings(venue=venue)
    base_url = venue["url"]

    resp = fetch(base_url, session)
    if resp is None and base_url.startswith("https://") and "www." not in base_url:
        alt = base_url.replace("https://", "https://www.")
        resp = fetch(alt, session)
    if resp is None:
        f.status = "unreachable"
        f.error = f"Could not fetch {base_url}"
        return f

    f.final_url = resp.url
    to_visit: list[str] = [resp.url]
    visited: set[str] = set()
    queued: set[str] = {resp.url.rstrip("/")}
    responses: list[requests.Response] = []

    while to_visit and len(visited) < MAX_PAGES_PER_SITE:
        url = to_visit.pop(0)
        if url.rstrip("/") in visited:
            continue
        page = resp if url == resp.url and not responses else fetch(url, session)
        if page is None:
            continue
        visited.add(url.rstrip("/"))
        responses.append(page)
        f.pages_crawled.append(page.url)

        soup = BeautifulSoup(page.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(page.url, a["href"]).split("#")[0]
            label = clean_text(a.get_text(" ", strip=True), 80)
            haystack = (href + " " + label).lower()
            if not any(w in haystack for w in INTERESTING_LINK_WORDS):
                continue
            if same_site(href, resp.url):
                if href.rstrip("/") not in queued and len(queued) < MAX_PAGES_PER_SITE * 3:
                    queued.add(href.rstrip("/"))
                    to_visit.append(href)
            else:
                # External booking link — often the actual booking system.
                if any(w in haystack for w in ["book", "reserv", "trybooking", "checkout"]):
                    entry = {"label": label or "(no label)", "url": href}
                    if entry not in f.booking_links:
                        f.booking_links.append(entry)
        time.sleep(CRAWL_DELAY_SECONDS)

    # Aggregate analysis over all fetched pages.
    all_html = "\n".join(r.text for r in responses)
    f.booking_platforms = detect_signatures(all_html, BOOKING_PLATFORM_SIGNATURES)
    f.cms = detect_signatures(all_html, CMS_SIGNATURES)
    phones: set[str] = set()
    for r in responses:
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
            p = normalise_phone(a["href"][4:])
            if p:
                phones.add(p)
        for m in PHONE_RE.findall(soup.get_text(" ", strip=True)):
            p = normalise_phone(m)
            if p:
                phones.add(p)
    f.phones = sorted(phones)[:5]
    f.emails = sorted({m.lower() for m in EMAIL_RE.findall(all_html)
                       if not m.lower().endswith((".png", ".jpg", ".svg", ".gif", ".webp", ".js", ".css"))
                       and "sentry" not in m.lower() and "example" not in m.lower()})[:5]
    f.socials = sorted({m for m in re.findall(
        r"https?://(?:www\.)?(?:instagram\.com|facebook\.com|tiktok\.com)/[A-Za-z0-9_.\-]+", all_html)})[:6]

    for r in responses:
        soup = BeautifulSoup(r.text, "html.parser")
        if soup.title and soup.title.string:
            t = clean_text(soup.title.string, 120)
            if t not in f.page_titles:
                f.page_titles.append(t)
        for snip in extract_context_snippets(soup, PRICE_RE):
            if snip not in f.price_mentions:
                f.price_mentions.append(snip)
        day_pattern = re.compile("|".join(DAY_WORDS), re.I)
        for snip in extract_context_snippets(soup, day_pattern):
            if TIME_RE.search(snip) and snip not in f.hours_mentions:
                f.hours_mentions.append(snip)
        for offering in extract_offerings(soup):
            if offering not in f.offerings:
                f.offerings.append(offering)

    f.price_mentions = f.price_mentions[:25]
    f.hours_mentions = f.hours_mentions[:12]
    f.offerings = f.offerings[:20]
    f.booking_links = f.booking_links[:10]
    return f


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_venue_markdown(f: VenueFindings) -> str:
    v = f.venue
    lines = [
        f"## {v['name']} — {v['suburb']}",
        "",
        f"- **Activity:** {v['activity']}",
        f"- **What gets booked:** {v['what_gets_booked']}",
        f"- **Walk group:** {v['walk']}" + ("  ·  ⭐ first-visit target" if v.get("first_visit") else ""),
        f"- **Website:** {f.final_url or v['url']}",
    ]
    if v.get("notes"):
        lines.append(f"- **Research notes:** {v['notes']}")
    if f.status != "ok":
        lines += ["", f"> ⚠️ **Site unreachable during this run** — {f.error}. "
                      "Data below may be incomplete; verify manually before the visit.", ""]
        return "\n".join(lines)

    lines.append(f"- **Pages analysed:** {len(f.pages_crawled)}")
    if f.cms:
        lines.append(f"- **Site built on:** {', '.join(f.cms)}")
    lines.append("")

    lines.append("### Current booking stack")
    if f.booking_platforms:
        lines.append(f"Detected third-party booking platform(s): **{', '.join(f.booking_platforms)}**.")
    else:
        lines.append("No known third-party booking platform detected — bookings may be "
                     "phone/email based or use a custom system. (Good sign for a OneTap pitch.)")
    if f.booking_links:
        lines.append("")
        lines.append("External booking links found on the site:")
        for bl in f.booking_links:
            lines.append(f"- [{bl['label']}]({bl['url']})")
    lines.append("")

    if f.offerings:
        lines.append("### Bookable offerings mentioned on site")
        for o in f.offerings:
            lines.append(f"- {o}")
        lines.append("")

    if f.price_mentions:
        lines.append("### Pricing signals")
        for p in f.price_mentions:
            lines.append(f"- {p}")
        lines.append("")

    if f.hours_mentions:
        lines.append("### Opening hours signals")
        for h in f.hours_mentions:
            lines.append(f"- {h}")
        lines.append("")

    contact_bits = []
    if f.phones:
        contact_bits.append("**Phone:** " + " / ".join(f.phones))
    if f.emails:
        contact_bits.append("**Email:** " + " / ".join(f.emails))
    if f.socials:
        contact_bits.append("**Social:** " + " · ".join(f.socials))
    if contact_bits:
        lines.append("### Contact")
        lines += [f"- {c}" for c in contact_bits]
        lines.append("")

    lines.append("### Pages crawled")
    lines += [f"- {p}" for p in f.pages_crawled]
    lines.append("")
    return "\n".join(lines)


def render_summary_markdown(findings: list[VenueFindings], generated_at: str) -> str:
    lines = [
        "# OneTap — Founding Venue Intelligence Report",
        "",
        f"_Generated {generated_at}. Source: Target_Venue_OneTap shortlist (10 venues, "
        "slot-based hourly inventory, Sydney)._",
        "",
        "| # | Venue | Suburb | Activity | Booking platform detected | Site | Prices found | Contact |",
        "|---|-------|--------|----------|---------------------------|------|--------------|---------|",
    ]
    for i, f in enumerate(findings, 1):
        v = f.venue
        platform = ", ".join(f.booking_platforms) or ("—" if f.status == "ok" else "site unreachable")
        contact = f.phones[0] if f.phones else (f.emails[0] if f.emails else "—")
        site = "ok" if f.status == "ok" else "❌"
        star = " ⭐" if v.get("first_visit") else ""
        lines.append(
            f"| {i} | {v['name']}{star} | {v['suburb']} | {v['activity']} | {platform} | {site} | "
            f"{len(f.price_mentions)} | {contact} |")
    lines += [
        "",
        "⭐ = first-visit target (owner in the building beats head office; visit weekday 2–4pm).",
        "",
        "---",
        "",
    ]
    for f in findings:
        lines.append(render_venue_markdown(f))
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def findings_to_dict(f: VenueFindings) -> dict:
    d = {k: v for k, v in f.__dict__.items()}
    return d


def run(venues_path: str | Path, out_dir: str | Path) -> dict:
    """Crawl every venue and write reports. Returns a manifest dict."""
    venues = json.loads(Path(venues_path).read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    findings: list[VenueFindings] = []
    for venue in venues:
        print(f"[{venue['id']}] crawling {venue['url']} ...", flush=True)
        try:
            f = crawl_venue(venue, session)
        except Exception as exc:  # keep one venue's failure from killing the run
            f = VenueFindings(venue=venue, status="error", error=repr(exc))
        print(f"[{venue['id']}] {f.status}: {len(f.pages_crawled)} pages, "
              f"platforms={f.booking_platforms}, prices={len(f.price_mentions)}", flush=True)
        findings.append(f)

    generated_at = datetime.now(ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%d %H:%M %Z")
    summary_md = render_summary_markdown(findings, generated_at)
    (out / "REPORT.md").write_text(summary_md)
    (out / "report_data.json").write_text(
        json.dumps({"generated_at": generated_at,
                    "venues": [findings_to_dict(f) for f in findings]}, indent=2))
    for f in findings:
        (out / f"{f.venue['id']}.md").write_text(render_venue_markdown(f))

    try:
        import render_html
        (out / "REPORT.html").write_text(
            render_html.render(json.loads((out / "report_data.json").read_text())))
    except Exception as exc:
        print(f"HTML render skipped: {exc!r}", flush=True)

    return {
        "generated_at": generated_at,
        "venues": len(findings),
        "ok": sum(1 for f in findings if f.status == "ok"),
        "failed": [f.venue["id"] for f in findings if f.status != "ok"],
        "report": str(out / "REPORT.md"),
    }


if __name__ == "__main__":
    here = Path(__file__).parent
    out_dir = sys.argv[1] if len(sys.argv) > 1 else here / "reports"
    manifest = run(here / "venues.json", out_dir)
    print(json.dumps(manifest, indent=2))
