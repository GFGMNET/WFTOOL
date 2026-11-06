# Plan: phase 5 (audit and reports) and phase 6 (setup and operation)

State: plan, 6 Nov 2026. Phases 1–4 are done. Each phase ends with a push to
`main` and a stop for review.

## Short version

**To decide** (recommendation in brackets; answer only where you want something else):

| # | Phase | Question | Recommendation |
| --- | --- | --- | --- |
| 1 | 5 | Daily tamper-check email to the admin: at what time? | 06:00 |
| 2 | 5 | May auditors see secret values (PIN, PUK)? | No, always masked |
| 3 | 5 | CSV export format | Semicolon, UTF-8 for Excel |
| 4 | 5 | Retention (design: built in, off until agreed with the data protection officer): what should it do when switched on? | Remove IP address and browser from requests closed more than N months ago; decisions and PDFs stay |
| 5 | 6 | Mail server for testing | A test mail server in the Docker setup whose emails you can read in the browser (Mailpit); your Postfix only for real sending |
| 6 | 6 | Production web server inside the container | waitress (one process, as the design requires) |

**What I will do:** phase 5, review stop, phase 6, review stop. Details below.

**What I need from you:**
- Phase 5: nothing.
- Phase 6: four facts about your server (can be answered later, before phase 6 starts):
  1. Operating system of the server that runs Docker (Linux or Windows).
  2. Which reverse proxy you use (nginx, IIS, Traefik, Caddy or none yet), and the app's address, e.g. `https://wftool.company.local`.
  3. Your AD group names to export (HR, IT, …) and the OU of the users, for the PowerShell export script. Without them I use example values.
  4. Your Postfix (or other) mail server: host, port, encryption, and whether it needs a login.

---

## Phase 5: audit and reports

Goal (design): "show me yesterday's request and who approved it" is one search and one click.

### What phase 5 delivers

1. **Search page** (admin and auditor; starters see their own requests):
   by date range, workflow, person (starter or anyone who acted), status and
   request number. Results as a list with paging.
2. **CSV export** of any search result: one row per request with number,
   workflow, status, starter, dates, current step, and the decisions; secret
   values never included.
3. **Full PDF report** per request (extends the phase 4 PDF):
   - form data of the final version;
   - every step: decision, person, time, IP address, browser, reason;
   - the signature image and fingerprint, or "signed on paper" with the scan's
     fingerprint, and the scan itself attached (encrypted at rest);
   - earlier form versions when the request was rejected and resent, with
     what changed;
   - the event history ("link fetched" shown separately, never as proof);
   - the result of the tamper check for this request's entries.
4. **Audit page**: the whole event log with filters (date, type, person,
   request), plus a "Check the chain now" button (`audit.verify()`), showing
   the first broken entry if there is one.
5. **Daily anchor email**: once a day the scheduler emails the latest
   fingerprint of the event log to the admin email, and stores that it did.
   The audit page lists the anchors sent, so deleting or rebuilding entries
   no longer matches what was emailed.
6. **Secret values** masked everywhere in lists, CSV and the audit page; the
   log records that a secret was viewed or printed, never its value.
7. **Retention setting** (default off): when switched on, the scheduler applies
   the agreed rule (decision 4) to requests closed longer than the set
   number of months, and logs each run. It cannot be switched on without the
   number of months.

### Tests

Search filters and permissions (starter only own, auditor read-only, no
secrets for auditors), CSV content and encoding, PDF content (pypdf text
check, earlier versions, signature, paper scan), tamper check detecting a
changed and a deleted entry, anchor email once per day, retention dry run and
real run.

### Done when

- Yesterday's approved request is found with one search and its PDF opened with one click.
- A changed or deleted event is reported by the tamper check, in the app and in the PDF.
- The anchor email arrives once a day at the local test mail server.

---

## Phase 6: setup and operation

Goal (design): one Docker container with one data folder, plus your mail
server, your reverse proxy and the PowerShell export.

### What phase 6 delivers

1. **Docker image** (`Dockerfile`, Python slim base, no build tools at run time):
   - app and scheduler in one process with waitress (decision 6);
   - runs as a non-root user;
   - `/data` volume for the database, import folder and files;
   - the key file mounted separately (read-only), never inside `/data`;
   - database changes run automatically on start (migrations exist since phase 1).
2. **docker-compose.yml** with the app and, for testing, Mailpit
   (decision 5): every email the app sends is visible at
   `http://server:8025`. For production, Settings point to your Postfix instead.
3. **Reverse proxy examples** for the proxy you name (nginx, IIS or Traefik):
   HTTPS, forwarded headers, secure cookies (`WFTOOL_SECURE_COOKIE=1`).
4. **Backup and update**:
   - `python -m wftool backup <file>`: consistent SQLite copy while the app runs (online backup);
   - written steps for backing up `/data` and the key file separately;
   - update steps: pull the new image, restart; the data stays.
5. **Health page** (admin): last import, emails waiting or failed, last daily
   anchor, open tasks past their reminder, disk space of the data folder.
6. **PowerShell export script** `scripts/export-ad.ps1`: writes `users.csv`
   and `groups.csv` (UTF-8, semicolon) for the groups you name, run hourly by
   Windows Task Scheduler under a read-only service account; plus the
   instructions for `software.csv`.
7. **End-to-end tests**: the HR Onboard and IT Assets Checklist runs (start,
   email links, signature, paper scan, rejection and resend, reminder,
   expiry), against a running app with a local test mail server. The browser
   checks done by hand in phases 3 and 4 become a script in the repository
   (Playwright, optional: skipped where it is not installed).
8. **Installation guide** in the README (German and English): first start,
   first sign-in, settings, CSV import, building the two example workflows
   or importing them, go-live checklist (including the works council and
   data protection check from the design).

### Limits of the build environment

Network access for package installs is off here. The Docker image installs
its packages (Flask, cryptography, reportlab, waitress) when it is built on
your server, so the image build and the container start can only be fully
tested there. Everything else is tested here as before.

### Done when

- `docker compose up` starts the app; sign-in, a full HR Onboard run and the
  emails in Mailpit work on a fresh server.
- A backup can be restored into a new container and verifies (tamper check ok).
- The README guides a first installation without further help.

---

## Order and size (estimate from the design, not a measurement)

| Phase | Lines (est.) | Review stop |
| --- | --- | --- |
| 5. Audit and reports | 800–1,000 | after push |
| 6. Setup and operation | 500–700 plus scripts and docs | after push |
