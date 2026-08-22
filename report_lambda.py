"""AWS Lambda entrypoint for the wasted-slot report.

Downloads the S3-hosted SQLite DB the tracker has been filling, builds the
report, and uploads TRACKING_REPORT.md / .html / .json to S3 under a
date-stamped prefix plus tracking/latest/.
"""

import json
import mimetypes
import os
from pathlib import Path

import boto3

import render_tracking_html
import report

BUCKET = os.environ["REPORT_BUCKET"]
DB_KEY = os.environ.get("DB_KEY", "state/tracking.db")
LOCAL_DB = "/tmp/tracking.db"
s3 = boto3.client("s3")


def handler(event, context):
    from datetime import datetime, timezone
    generated = report.now_sydney_str()
    out = Path("/tmp/reports")
    out.mkdir(parents=True, exist_ok=True)

    s3.download_file(BUCKET, DB_KEY, LOCAL_DB)
    outcomes, meta = report.load_outcomes(LOCAL_DB)
    stats = report.aggregate(outcomes)
    data = report.to_json(stats, meta, generated)

    (out / "tracking_report.json").write_text(json.dumps(data, indent=2))
    (out / "TRACKING_REPORT.md").write_text(report.render_markdown(stats, meta, generated))
    (out / "TRACKING_REPORT.html").write_text(render_tracking_html.render(data))

    date_prefix = generated[:10]
    uploaded = []
    for f in out.iterdir():
        ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
        for prefix in (f"tracking/{date_prefix}", "tracking/latest"):
            s3.upload_file(str(f), BUCKET, f"{prefix}/{f.name}", ExtraArgs={"ContentType": ctype})
        uploaded.append(f.name)

    result = {"generated_at": generated, "bucket": BUCKET,
              "s3_prefix": f"tracking/{date_prefix}/", "files": uploaded,
              "totals": {"sold": sum(s.sold for s in stats.values()),
                         "wasted": sum(s.wasted for s in stats.values())}}
    print(json.dumps(result))
    return result
