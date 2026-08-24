"""Per-platform adapters that turn a booking system's live availability into a
uniform list of Slot observations.

Each adapter exposes: fetch(target, session) -> list[Slot].
`target` is one entry from tracking_targets.json.

Add a venue by adding a target entry with the right "platform" and, if the
platform is new, an adapter function registered in ADAPTERS below.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from store import Slot

SYDNEY = ZoneInfo("Australia/Sydney")
LOOKAHEAD_DAYS = 14           # how far forward to observe slots
REQUEST_TIMEOUT = 20
CRAWL_DELAY_SECONDS = 0.6     # pause between requests to small/self-hosted sites


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_local(s: str, tz: ZoneInfo) -> datetime:
    """Parse a naive local ISO timestamp (FareHarbor start_at) as venue-local time."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


# ---------------------------------------------------------------------------
# FareHarbor  (Kiss My Axe)  — public JSON API, no auth
# ---------------------------------------------------------------------------

def fareharbor(target: dict, session: requests.Session) -> list[Slot]:
    shortname = target["shortname"]
    tz = ZoneInfo(target.get("timezone", "Australia/Sydney"))
    base = f"https://fareharbor.com/api/v1/companies/{shortname}"

    # Resolve the items to track. If the target names items explicitly use those,
    # otherwise auto-discover and skip obvious non-slot products.
    default_price = target.get("default_price")
    lookahead = int(target.get("lookahead_days", LOOKAHEAD_DAYS))
    items = target.get("items")
    if not items:
        r = session.get(f"{base}/items/", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        skip = ("gift card", "voucher", "notes", "membership", "club")
        items = [{"id": str(it["pk"]), "name": it.get("name", str(it["pk"]))}
                 for it in r.json().get("items", [])
                 if not any(w in (it.get("name") or "").lower() for w in skip)]

    today = datetime.now(tz).date()
    slots: list[Slot] = []
    for item in items:
        item_id, item_name = str(item["id"]), item["name"]
        item_price = item.get("price", default_price)
        for offset in range(lookahead):
            day = today + timedelta(days=offset)
            url = f"{base}/items/{item_id}/availabilities/date/{day.isoformat()}/"
            try:
                r = session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    continue
                data = r.json()
            except (requests.RequestException, ValueError):
                continue
            for a in data.get("availabilities", []):
                start = _parse_local(a["start_at"], tz)
                end = _parse_local(a["end_at"], tz)
                # A slot is "wasted" only if it got ZERO bookings by its start time.
                # has_customers is the reliable signal here: is_bookable just tracks the
                # ~1h online booking cutoff (not sales), and is_sold_out never trips
                # because capacity is high; capacity numbers are hidden. So available==empty.
                available = not bool(a.get("has_customers"))
                slots.append(Slot(
                    item_id=item_id,
                    item_name=item_name,
                    slot_start_utc=_iso_utc(start),
                    slot_end_utc=_iso_utc(end),
                    slot_local=start.strftime("%Y-%m-%d %H:%M"),
                    is_available=available,
                    capacity=None,  # FareHarbor's approximate_available_capacity is unreliable (often 0)
                    price=item_price,
                ))
    return slots


# ---------------------------------------------------------------------------
# YourGolfBooking  (Moore Park driving range) — public API, no auth
# ---------------------------------------------------------------------------
# A capacity venue: instead of one bookable slot, the range has N bays. We track
# how many bays sit idle each operating hour. The public API gives the full bay
# list and every booked bay-slot for a day; free = total bays - bays booked in
# that hour. is_available is True whenever any bay is free.

def yourgolfbooking(target: dict, session: requests.Session) -> list[Slot]:
    base = target.get("api_base", "https://api.yourgolfbooking.com")
    slug = target["slug"]
    tz = ZoneInfo(target.get("timezone", "Australia/Sydney"))
    lookahead = int(target.get("lookahead_days", 5))
    # Operating window per weekday (24h local). Default applies unless overridden.
    hours = target.get("hours", {})
    default_open, default_close = hours.get("default", [6, 22])
    dead_statuses = {"cancelled", "canceled", "no-show", "noshow", "refunded", "abandoned"}

    price_per_unit_hour = target.get("price_per_unit_hour")
    r = session.get(f"{base}/venue/{slug}/bays", timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    total_bays = sum(1 for b in r.json() if b.get("bookable"))
    if not total_bays:
        return []

    today = datetime.now(tz).date()
    slots: list[Slot] = []
    for offset in range(lookahead):
        day = today + timedelta(days=offset)
        wk = day.strftime("%a").upper()[:3]        # MON, TUE, ...
        day_open, day_close = hours.get(wk, [default_open, default_close])

        # Bookings for this local day (API caps the range at one day).
        day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz)
        day_end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=tz)
        try:
            resp = session.get(
                f"{base}/venue/{slug}/bookings/public",
                params={"start_gte": _iso_ms(day_start), "start_lte": _iso_ms(day_end)},
                timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            bookings = resp.json()
        except (requests.RequestException, ValueError):
            continue

        # Booked distinct bays per local hour.
        booked_by_hour: dict[int, set] = {}
        for b in bookings:
            if str(b.get("status", "")).lower() in dead_statuses:
                continue
            try:
                bs = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz)
                be = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz)
            except (KeyError, ValueError):
                continue
            h = bs.replace(minute=0, second=0, microsecond=0)
            while h < be:
                if h.date() == day:
                    booked_by_hour.setdefault(h.hour, set()).add(b.get("bayId", id(b)))
                h += timedelta(hours=1)

        for hr in range(day_open, day_close):
            start = datetime(day.year, day.month, day.day, hr, 0, tzinfo=tz)
            end = start + timedelta(hours=1)
            booked = len(booked_by_hour.get(hr, ()))
            free = max(0, total_bays - booked)
            slots.append(Slot(
                item_id="range-bays",
                item_name="Driving Range bays",
                slot_start_utc=_iso_utc(start),
                slot_end_utc=_iso_utc(end),
                slot_local=start.strftime("%Y-%m-%d %H:%M"),
                is_available=free > 0,
                capacity=free,
                capacity_total=total_bays,
                price=price_per_unit_hour,   # per idle bay-hour
            ))
    return slots


def _iso_ms(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------
# Intrac  (Camperdown Tennis, City Community Tennis) — court grid behind a login
# ---------------------------------------------------------------------------
# The booking grid (booking.cfm) redirects to login.cfm unless authenticated.
# This adapter logs in with credentials from env vars, then parses the court x
# time grid. It is OFF by default (targets have "enabled": false) because it
# needs a real member account and the grid markup should be verified against a
# live logged-in session before trusting the numbers.

def intrac(target: dict, session: requests.Session) -> list[Slot]:
    host = target["host"]                      # e.g. camperdowntennis.intrac.com.au
    user = os.environ.get(target.get("username_env", "INTRAC_USERNAME"))
    pw = os.environ.get(target.get("password_env", "INTRAC_PASSWORD"))
    if not user or not pw:
        raise RuntimeError(
            f"Intrac venue '{target['venue_id']}' needs a login. Set "
            f"{target.get('username_env', 'INTRAC_USERNAME')} and "
            f"{target.get('password_env', 'INTRAC_PASSWORD')} in the environment.")

    tz = ZoneInfo(target.get("timezone", "Australia/Sydney"))
    login_url = f"https://{host}/tennis/login.cfm"
    session.post(login_url, data={"username": user, "password": pw, "login": "Login"},
                 timeout=REQUEST_TIMEOUT, verify=False)

    today = datetime.now(tz).date()
    slots: list[Slot] = []
    for offset in range(LOOKAHEAD_DAYS):
        day = today + timedelta(days=offset)
        url = f"https://{host}/tennis/booking.cfm?date={day.strftime('%d/%m/%Y')}"
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT, verify=False)
            if r.status_code != 200 or "login" in r.url.lower():
                continue
        except requests.RequestException:
            continue
        slots.extend(_parse_intrac_grid(r.text, day, tz))
    return slots


def _parse_intrac_grid(html: str, day, tz: ZoneInfo) -> list[Slot]:
    """Parse an Intrac booking grid into hourly court slots.

    Intrac renders a table where each row is a time and each cell is a court.
    A free cell links to a booking form / has a 'available' class; a taken cell
    shows a name or a 'booked'/'unavailable' class. Markup varies per install,
    so this reads defensively and treats a cell as available when it links to a
    booking action or is explicitly classed available.

    VERIFY the class names against your logged-in grid before trusting output.
    """
    soup = BeautifulSoup(html, "html.parser")
    slots: list[Slot] = []
    for row in soup.select("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        time_txt = cells[0].get_text(strip=True)
        start = _intrac_time(day, time_txt, tz)
        if start is None:
            continue
        end = start + timedelta(hours=1)
        for ci, cell in enumerate(cells[1:], start=1):
            classes = " ".join(cell.get("class", [])).lower()
            text = cell.get_text(" ", strip=True).lower()
            has_book_link = bool(cell.find("a", href=True) and
                                 any(w in (cell.find("a")["href"] or "").lower()
                                     for w in ("book", "add", "reserve")))
            available = ("available" in classes or "free" in classes or has_book_link) and \
                        not any(w in classes for w in ("booked", "unavailable", "closed")) and \
                        not any(w in text for w in ("booked", "unavailable", "closed"))
            slots.append(Slot(
                item_id=f"court{ci}",
                item_name=f"Court {ci}",
                slot_start_utc=_iso_utc(start),
                slot_end_utc=_iso_utc(end),
                slot_local=start.strftime("%Y-%m-%d %H:%M"),
                is_available=available,
                capacity=1 if available else 0,
            ))
    return slots


def _intrac_time(day, time_txt: str, tz: ZoneInfo) -> datetime | None:
    import re
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_txt, re.I)
    if not m:
        return None
    hour = int(m.group(1)); minute = int(m.group(2) or 0)
    ap = (m.group(3) or "").lower()
    if ap == "pm" and hour != 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23):
        return None
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


# ---------------------------------------------------------------------------
# The Great Escape (custom WooCommerce theme) — nonce-gated availability AJAX
# ---------------------------------------------------------------------------
# The booking theme exposes each day's sessions (with seat counts + status) via
# a single admin-ajax call, `game_overview_load`, protected by a standard
# WordPress nonce that the site serves openly in the page. We read it exactly
# as the booking UI does — one request per day — and treat a session that still
# has ALL seats free at its start as "empty" (zero bookings = wasted).

def greatescape(target: dict, session: requests.Session) -> list[Slot]:
    tz = ZoneInfo(target.get("timezone", "Australia/Sydney"))
    overview = target["overview_page"]
    ajax = target["ajax_url"]
    venue = target.get("venue", "sydney")
    lookahead = int(target.get("lookahead_days", 2))
    price = target.get("price_per_empty", 130)   # min booking value of an empty room

    # 1) Fresh nonce from the overview page (nonces expire, so grab per poll).
    page = session.get(overview, timeout=REQUEST_TIMEOUT)
    page.raise_for_status()
    m = re.search(r'game_overview_ajax_variable\s*=\s*\{[^}]*"ajax_nonce":"([a-z0-9]+)"', page.text)
    if not m:
        raise RuntimeError("Great Escape: could not read booking nonce from page")
    nonce = m.group(1)

    today = datetime.now(tz).date()
    slots: list[Slot] = []
    for offset in range(lookahead):
        day = today + timedelta(days=offset)
        try:
            r = session.post(ajax, timeout=REQUEST_TIMEOUT, data={
                "action": "game_overview_load", "security": nonce, "layout_column": "3",
                "date": day.isoformat(), "venue": venue, "sortby": "", "sortby_order": ""})
            if r.status_code != 200:
                continue
            posts = r.json().get("data", {}).get("posts", "")
        except (requests.RequestException, ValueError):
            continue
        soup = BeautifulSoup(posts, "html.parser")
        for row in soup.select(".game-list-row"):
            room = row.get("data-product-name") or ""
            if not room:
                h = row.select_one("h2, h3, .game-list-row-title, a")
                room = h.get_text(strip=True) if h else "Escape Room"
            try:
                maxp = int(row.get("data-maximum-players") or 0)
            except ValueError:
                maxp = 0
            for sp in row.select(".time-slot"):
                start_s = sp.get("data-eventstarttime")
                end_s = sp.get("data-eventendtime")
                if not start_s:
                    continue
                try:
                    start = datetime.fromisoformat(start_s)
                    end = datetime.fromisoformat(end_s) if end_s else start + timedelta(hours=1)
                except ValueError:
                    continue
                if start.tzinfo is None:
                    start = start.replace(tzinfo=tz)
                    end = end.replace(tzinfo=tz)
                try:
                    seats = int(sp.get("data-numseatsavailable") or 0)
                except ValueError:
                    seats = 0
                status = (sp.get("data-localstatus") or "").lower()
                # Empty (zero bookings) = all seats still free and bookable.
                empty = status == "available" and (maxp == 0 or seats >= maxp)
                slots.append(Slot(
                    item_id=room or "escape-room",
                    item_name=room or "Escape Room",
                    slot_start_utc=_iso_utc(start),
                    slot_end_utc=_iso_utc(end),
                    slot_local=start.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                    is_available=empty,
                    price=price,
                ))
        time.sleep(CRAWL_DELAY_SECONDS)   # be gentle on their WordPress
    return slots


# ---------------------------------------------------------------------------
# Yepbooking (NBC Badminton — Alexandria) — public court grid, no login
# ---------------------------------------------------------------------------
# A capacity venue like the driving range: N courts booked by the hour. The
# schedule grid is served openly via ajax.schema.php per day/location; each cell
# shows Available or Booked. We count free courts per operating hour.

def _yep_hour(label: str):
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap]m)", label, re.I)
    if not m:
        return None
    h, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ap == "pm" and h != 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    return h, mm


def yepbooking(target: dict, session: requests.Session) -> list[Slot]:
    tz = ZoneInfo(target.get("timezone", "Australia/Sydney"))
    base = target["base"].rstrip("/")
    id_sport = str(target["id_sport"])
    price = target.get("price_per_unit_hour", 30)
    lookahead = int(target.get("lookahead_days", 3))
    item_name = target.get("court_label", "Courts")

    session.get(base + "/", timeout=REQUEST_TIMEOUT)   # establish session cookie
    today = datetime.now(tz).date()
    slots: list[Slot] = []
    for offset in range(lookahead):
        day = today + timedelta(days=offset)
        try:
            r = session.post(base + "/ajax/ajax.schema.php", timeout=REQUEST_TIMEOUT, data={
                "id_sport": id_sport, "day": str(day.day), "month": str(day.month),
                "year": str(day.year), "event": "", "timetableWidth": "1200",
                "arLabelId": "", "noticeCheckRequested": "1"})
            if r.status_code != 200 or not r.text.strip():
                continue
        except requests.RequestException:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        # per court row -> per start-hour state (Booked wins over Available)
        free: dict[str, int] = {}
        booked: dict[str, int] = {}
        for tr in soup.select("tr[class*=trSchemaLane]"):
            per_hour: dict[str, str] = {}
            for el in tr.find_all(["td", "a"]):
                title = el.get("title")
                if not title or " - " not in title:
                    continue
                state = title.rsplit(" - ", 1)[-1]
                if state not in ("Available", "Booked"):
                    continue
                hm = re.match(r"(\d{1,2}:\d{2}\s*[ap]m)", title, re.I)
                if not hm:
                    continue
                hr = hm.group(1).replace(" ", "")
                if hr not in per_hour or state == "Booked":
                    per_hour[hr] = state
            for hr, st in per_hour.items():
                if st == "Available":
                    free[hr] = free.get(hr, 0) + 1
                else:
                    booked[hr] = booked.get(hr, 0) + 1

        for hr in sorted(set(free) | set(booked)):
            hp = _yep_hour(hr)
            if hp is None:
                continue
            f = free.get(hr, 0)
            total = f + booked.get(hr, 0)
            if total == 0:
                continue
            start = datetime(day.year, day.month, day.day, hp[0], hp[1], tzinfo=tz)
            end = start + timedelta(hours=1)
            slots.append(Slot(
                item_id="courts",
                item_name=item_name,
                slot_start_utc=_iso_utc(start),
                slot_end_utc=_iso_utc(end),
                slot_local=start.strftime("%Y-%m-%d %H:%M"),
                is_available=f > 0,
                capacity=f,
                capacity_total=total,
                price=price,
            ))
        time.sleep(CRAWL_DELAY_SECONDS)
    return slots


ADAPTERS = {
    "fareharbor": fareharbor,
    "intrac": intrac,
    "yourgolfbooking": yourgolfbooking,
    "greatescape": greatescape,
    "yepbooking": yepbooking,
}


def fetch(target: dict, session: requests.Session) -> list[Slot]:
    platform = target["platform"]
    if platform not in ADAPTERS:
        raise RuntimeError(f"No adapter for platform '{platform}'")
    return ADAPTERS[platform](target, session)
