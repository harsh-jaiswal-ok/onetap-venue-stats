# OneTap venue intelligence + wasted-slot tracking

Two tools for the 10 founding-venue targets (from `Target_Venue_OneTap.pdf`):

1. **Venue intelligence** (`venue_report.py`) — one-shot crawl of each venue's
   public website: booking platform in use, bookable offerings, pricing
   signals, opening hours, contacts. Detailed report per venue.
2. **Wasted-slot tracker** (`tracker.py` + `report.py`) — polls each
   *trackable* venue's live availability on a schedule for as long as you leave
   it running, then reports which slots went unsold ("wasted"). This is the
   idle-inventory pitch: "you wasted N sessions last week, mostly Tue–Thu 2–5pm."

## Files

| File | Purpose |
|------|---------|
| `venues.json` | The 10 target venues (intelligence crawl) |
| `venue_report.py` | Website crawler + intelligence report |
| `lambda_handler.py` / `template.yaml` | AWS deploy for the intelligence crawl |
| **`tracking_targets.json`** | Which venues to track live + their booking platform |
| **`booking_adapters.py`** | Per-platform availability readers (FareHarbor live; Intrac scaffold) |
| **`store.py`** | SQLite snapshot storage + `Slot` shape |
| **`tracker.py`** | Long-running poller — leave it running for a week |
| **`report.py`** | Reads snapshots → wasted-slot report (run any time) |
| **`render_tracking_html.py`** | Styled HTML for the wasted-slot report |
| **`tracker_lambda.py` / `report_lambda.py` / `template-tracker.yaml`** | AWS deploy for the tracker |
| **`build_site.py`** | Builds the static stats website into `docs/` for GitHub Pages |
| `requirements.txt` | `requests`, `beautifulsoup4`, `tzdata` |

---

## Static website (GitHub Pages)

`build_site.py` turns the two report JSON files into one self-contained page at
`docs/index.html` — a tabbed dashboard (Idle inventory · Venue intelligence)
with a light/dark toggle. It's plain HTML/CSS/JS (only Google Fonts loaded
externally), so GitHub Pages serves it as-is.

### Build it

```bash
python venue_report.py     # refresh venue intelligence  -> reports/report_data.json
python report.py           # refresh wasted-slot stats    -> reports/tracking_report.json
python build_site.py       # assemble docs/index.html
open docs/index.html       # preview locally (or: python -m http.server -d docs)
```

The data is baked into the page, so it works both on Pages and opened locally.
Re-run these three whenever you want to refresh, then commit `docs/`.

### Publish on GitHub

```bash
cd onetap-venue-reports
git init && git add . && git commit -m "OneTap venue stats site"
git branch -M main
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

Then on GitHub: **Settings → Pages → Build and deployment →** Source *Deploy
from a branch*, Branch **main** / **`/docs`** → Save. The site goes live at
`https://<you>.github.io/<repo>/` within a minute. (The included `docs/.nojekyll`
tells Pages to serve the files untouched.)

> Tip: to keep it fresh automatically, run the AWS tracker/report Lambdas (below),
> have the report Lambda also commit `reports/tracking_report.json` back to the
> repo — or just re-run the three build commands and `git push` on a cron.

---

## Wasted-slot tracking

### What's trackable

| Venue | Platform | Status |
|-------|----------|--------|
| **Kiss My Axe** | FareHarbor | ✅ **On** — public JSON API, no login |
| **Moore Park Driving Range** | YourGolfBooking | ✅ **On** — public API; tracked as idle bays per hour (capacity venue) |
| Camperdown Tennis | Intrac | ⚙️ Off — needs a member login (best court story once enabled) |
| City Community Tennis | Intrac | ⚙️ Off — needs a member login |
| Cipher Room | Bookeo | ❌ reCAPTCHA-gated |
| Maniax | custom widget | ❌ bot-protected |
| Dynasty / Maze / Soccer Club | — | ❌ email / phone / offline |
| Zero Latency / Great Escape | JS app | ❌ no open availability API |

### Run it locally (the "leave it for a week" flow)

```bash
pip install -r requirements.txt

# Start the poller in the background (survives closing the terminal):
nohup python tracker.py > tracker.log 2>&1 &

# ...come back any time and build the report:
python report.py
open reports/TRACKING_REPORT.html
```

- `tracker.py` polls every 30 min by default (`POLL_MINUTES=20 python tracker.py`
  to change) and appends to `tracking.db` (SQLite).
- A slot is **wasted** when the last check before its start time still showed it
  open. **Utilisation** = sold ÷ (sold + wasted). The report also ranks the
  "deadest windows" (weekday × hour) — the concrete idle-inventory pitch.
- Stop the tracker with `kill %1` (or find it with `pgrep -f tracker.py`).

### Enabling the two tennis venues (Intrac)

The court grid sits behind a member login, so it's off by default. To turn it on:

1. Get / create a member account for the venue's Intrac site.
2. Export the credentials the target names, e.g.:
   ```bash
   export CAMPERDOWN_INTRAC_USER=you@example.com
   export CAMPERDOWN_INTRAC_PASS=•••••
   ```
3. **Verify the grid markup**: the Intrac adapter in `booking_adapters.py`
   parses the court×time table defensively, but class names vary per install —
   log in once, view `booking.cfm`, and confirm the available/booked cell
   classes match `_parse_intrac_grid`.
4. Set `"enabled": true` for that venue in `tracking_targets.json`.

### Deploy the tracker to AWS (SAM)

```bash
sam build -t template-tracker.yaml
sam deploy --guided --stack-name onetap-tracker -t template-tracker.yaml
```

Creates two Lambdas and a private S3 bucket:

- **`onetap-tracker-poll`** — runs every 30 min (EventBridge), pulls the SQLite
  DB from `s3://onetap-tracker-<account-id>/state/tracking.db`, polls, pushes it
  back. Single scheduled writer, so no VPC/EFS needed.
- **`onetap-tracker-report`** — runs daily (default 22:00 UTC ≈ 8am Sydney),
  rebuilds the report and writes it to `tracking/latest/` and
  `tracking/YYYY-MM-DD/`.

```bash
# Report on demand, then fetch it:
aws lambda invoke --function-name onetap-tracker-report /dev/stdout
aws s3 cp s3://onetap-tracker-<account-id>/tracking/latest/TRACKING_REPORT.html .
```

To enable Intrac venues on AWS, add the credential env vars to `TrackerFunction`
in `template-tracker.yaml` (or wire them through SSM/Secrets Manager) and flip
`enabled` in `tracking_targets.json`.

---

## Venue intelligence crawl

The one-shot website crawl (booking platform, prices, hours, contacts).

### Run locally

```bash
pip install -r requirements.txt
python venue_report.py            # writes reports/ next to the script
open reports/REPORT.md
```

### Deploy to AWS (SAM)

Prereqs: AWS CLI configured (`aws configure`), and the SAM CLI
(`brew install aws-sam-cli`).

```bash
sam build
sam deploy --guided --stack-name onetap-venue-reports
```

That creates:

- **Lambda** `onetap-venue-report` (Python 3.12, 512 MB, 10-min timeout)
- **S3 bucket** `onetap-venue-reports-<account-id>` (private, 180-day retention)
- **EventBridge schedule** — every Sunday 21:00 UTC (Monday morning Sydney).
  Change with `--parameter-overrides Schedule="rate(1 day)"` etc.

Run it on demand and fetch the report:

```bash
aws lambda invoke --function-name onetap-venue-report /dev/stdout
aws s3 cp s3://onetap-venue-reports-<account-id>/reports/latest/REPORT.md .
```

Reports land under `reports/YYYY-MM-DD/` (history) and `reports/latest/`
(always the newest run).

## Notes

- The crawler is polite: ≤8 pages per site, 0.5 s delay between requests,
  browser User-Agent, 15 s timeouts. One venue failing never kills the run.
- JS-only sites (heavy Wix/React) may show fewer pricing signals — the report
  flags what it could and couldn't reach so you can verify before a visit.
- Booking-platform detection is signature-based (`BOOKING_PLATFORM_SIGNATURES`
  in `venue_report.py`); add signatures as you encounter new systems.
