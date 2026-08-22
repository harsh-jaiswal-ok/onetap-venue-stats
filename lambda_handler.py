"""AWS Lambda entrypoint.

Crawls all venues, writes reports to /tmp, then uploads them to S3 under a
date-stamped prefix plus a stable `latest/` prefix. Triggered on a schedule
by EventBridge (see template.yaml) or invoked manually.
"""

import json
import mimetypes
import os
from pathlib import Path

import boto3

import venue_report

BUCKET = os.environ["REPORT_BUCKET"]
s3 = boto3.client("s3")


def handler(event, context):
    out_dir = Path("/tmp/reports")
    manifest = venue_report.run(Path(__file__).parent / "venues.json", out_dir)

    date_prefix = manifest["generated_at"][:10]  # YYYY-MM-DD
    uploaded = []
    for file in out_dir.iterdir():
        content_type = mimetypes.guess_type(file.name)[0] or "text/markdown"
        for prefix in (f"reports/{date_prefix}", "reports/latest"):
            key = f"{prefix}/{file.name}"
            s3.upload_file(str(file), BUCKET, key,
                           ExtraArgs={"ContentType": content_type})
        uploaded.append(file.name)

    result = {**manifest,
              "bucket": BUCKET,
              "s3_prefix": f"reports/{date_prefix}/",
              "files": uploaded}
    print(json.dumps(result))
    return result
