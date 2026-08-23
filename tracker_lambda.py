"""AWS Lambda entrypoint for the availability tracker.

Lambda's filesystem is ephemeral, so the SQLite DB is persisted in S3: each
invocation downloads it, runs one poll (appending snapshots), uploads it back,
then rebuilds the dashboard and publishes it to the public site bucket.

A single EventBridge schedule drives this, so there is never concurrent
writing — no VPC, EFS or NAT needed.
"""

import json
import os
from pathlib import Path

import boto3

import build_site
import report
import tracker

BUCKET = os.environ["REPORT_BUCKET"]
SITE_BUCKET = os.environ.get("SITE_BUCKET")
DB_KEY = os.environ.get("DB_KEY", "state/tracking.db")
LOCAL_DB = "/tmp/tracking.db"
s3 = boto3.client("s3")

# Venue-intelligence data is bundled with the deployment (slow-changing); the
# dashboard shows it on the "Venue intelligence" tab.
INTEL_PATH = Path(__file__).parent / "reports" / "report_data.json"


def _pull():
    try:
        s3.download_file(BUCKET, DB_KEY, LOCAL_DB)
    except s3.exceptions.ClientError:
        pass  # first run — no DB yet, tracker will create it


def _push():
    s3.upload_file(LOCAL_DB, BUCKET, DB_KEY)


def _publish_site():
    if not SITE_BUCKET:
        return
    generated = report.now_sydney_str()
    outcomes, meta = report.load_outcomes(LOCAL_DB)
    stats = report.aggregate(outcomes)
    tracking = report.to_json(stats, meta, generated)
    intel = None
    if INTEL_PATH.exists():
        try:
            intel = json.loads(INTEL_PATH.read_text())
        except (ValueError, OSError):
            intel = None
    html = build_site.render_page(intel, tracking)
    s3.put_object(Bucket=SITE_BUCKET, Key="index.html",
                  Body=html.encode("utf-8"), ContentType="text/html; charset=utf-8",
                  CacheControl="no-cache")
    # also keep the machine-readable data next to it
    s3.put_object(Bucket=SITE_BUCKET, Key="tracking_report.json",
                  Body=json.dumps(tracking, indent=2).encode("utf-8"),
                  ContentType="application/json", CacheControl="no-cache")


def handler(event, context):
    _pull()
    summary = tracker.poll_once(LOCAL_DB)
    _push()
    try:
        _publish_site()
        summary["site_published"] = bool(SITE_BUCKET)
    except Exception as exc:  # never let publishing failure lose the poll
        print(f"site publish failed: {exc!r}")
        summary["site_error"] = repr(exc)
    print(json.dumps(summary))
    return summary
