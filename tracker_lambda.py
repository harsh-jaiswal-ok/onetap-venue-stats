"""AWS Lambda entrypoint for the availability tracker.

Lambda's filesystem is ephemeral, so the SQLite DB is persisted in S3: each
invocation downloads it, runs one poll (appending snapshots), and uploads it
back. A single EventBridge schedule drives this, so there is never concurrent
writing — no VPC, EFS or NAT needed.

Schedule every POLL_MINUTES via EventBridge (see template-tracker.yaml).
"""

import os

import boto3

import tracker

BUCKET = os.environ["REPORT_BUCKET"]
DB_KEY = os.environ.get("DB_KEY", "state/tracking.db")
LOCAL_DB = "/tmp/tracking.db"
s3 = boto3.client("s3")


def _pull():
    try:
        s3.download_file(BUCKET, DB_KEY, LOCAL_DB)
    except s3.exceptions.ClientError:
        pass  # first run — no DB yet, tracker will create it


def _push():
    s3.upload_file(LOCAL_DB, BUCKET, DB_KEY)


def handler(event, context):
    _pull()
    summary = tracker.poll_once(LOCAL_DB)
    _push()
    print(summary)
    return summary
