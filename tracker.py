"""Availability tracker — polls every enabled venue on an interval and records
a snapshot of which slots are still open. Leave it running for as long as you
want to collect data (a week gives a solid picture); run report.py any time.

Usage:
    python tracker.py                 # loop forever, poll every POLL_MINUTES
    python tracker.py --once          # single poll then exit (for cron / Lambda)
    POLL_MINUTES=20 python tracker.py # override interval

Run it in the background so it survives your terminal closing:
    nohup python tracker.py > tracker.log 2>&1 &
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

import booking_adapters
import store

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
POLL_MINUTES = int(os.environ.get("POLL_MINUTES", "30"))
TARGETS_PATH = Path(__file__).parent / "tracking_targets.json"


def poll_once(db_path: Path | str = store.DEFAULT_DB) -> dict:
    targets = [t for t in json.loads(TARGETS_PATH.read_text()) if t.get("enabled")]
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    summary = {"observed_at": observed_at, "venues": {}}
    with store.connect(db_path) as conn:
        for t in targets:
            vid = t["venue_id"]
            try:
                slots = booking_adapters.fetch(t, session)
                n = store.record_snapshot(conn, vid, t["venue_name"], observed_at, slots)
                store.record_poll(conn, observed_at, vid, "ok", n)
                available = sum(1 for s in slots if s.is_available)
                summary["venues"][vid] = {"slots": n, "available_now": available}
                print(f"[{observed_at}] {vid}: {n} slots ({available} open)", flush=True)
            except Exception as exc:
                store.record_poll(conn, observed_at, vid, "error", 0, repr(exc))
                summary["venues"][vid] = {"error": repr(exc)}
                print(f"[{observed_at}] {vid}: ERROR {exc!r}", flush=True)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    ap.add_argument("--db", default=str(store.DEFAULT_DB))
    args = ap.parse_args()

    if args.once:
        poll_once(args.db)
        return

    print(f"Tracker started. Polling every {POLL_MINUTES} min. Ctrl-C to stop.", flush=True)
    while True:
        try:
            poll_once(args.db)
        except KeyboardInterrupt:
            print("Stopped.", flush=True)
            break
        except Exception as exc:
            print(f"poll failed: {exc!r}", flush=True)
        time.sleep(POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
