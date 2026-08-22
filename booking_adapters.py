"""Per-platform adapters that turn a booking system's live availability into a
uniform list of Slot observations.

Each adapter exposes: fetch(target, session) -> list[Slot].
`target` is one entry from tracking_targets.json.

Add a venue by adding a target entry with the right "platform" and, if the
platform is new, an adapter function registered in ADAPTERS below.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from store import Slot

SYDNEY = ZoneInfo("Australia/Sydney")
LOOKAHEAD_DAYS = 14           # how far forward to observe slots
REQUEST_TIMEOUT = 20


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
        for offset in range(LOOKAHEAD_DAYS):
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
                # Available == the platform still lets you book it and it isn't sold out.
                available = bool(a.get("is_bookable")) and not a.get("is_sold_out", False)
                slots.append(Slot(
                    item_id=item_id,
                    item_name=item_name,
                    slot_start_utc=_iso_utc(start),
                    slot_end_utc=_iso_utc(end),
                    slot_local=start.strftime("%Y-%m-%d %H:%M"),
                    is_available=available,
                    capacity=a.get("approximate_available_capacity"),
                ))
    return slots


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


ADAPTERS = {
    "fareharbor": fareharbor,
    "intrac": intrac,
}


def fetch(target: dict, session: requests.Session) -> list[Slot]:
    platform = target["platform"]
    if platform not in ADAPTERS:
        raise RuntimeError(f"No adapter for platform '{platform}'")
    return ADAPTERS[platform](target, session)
