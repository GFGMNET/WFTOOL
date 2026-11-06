# Plan: phase 5 (audit and reports) and phase 6 (setup and operation)

State: plan, 6 Nov 2026. Phases 1–4 are done. Each phase ends with a push to
`main` and a stop for review.

## Short version

**Decisions** (all decided by the owner on 6 Nov 2026):

| # | Phase | Question | Recommendation |
| --- | --- | --- | --- |
| 1 | 5 | Daily tamper-check email to the admin: at what time? | **Decided 6 Nov 2026: 06:00** |
| 2 | 5 | May auditors see secret values (PIN, PUK)? | **Decided 6 Nov 2026: yes, always visible to auditors** |
| 3 | 5 | CSV export format | **Decided 6 Nov 2026: Semicolon, UTF-8 for Excel** |
| 4 | 5 | Retention (design: built in, off until agreed with the data protection officer): what should it do when switched on? | **Decided 6 Nov 2026: Remove IP address and browser from requests closed more than N months ago; decisions and PDFs stay** |
| 5 | 6 | Mail server for testing | **Decided 6 Nov 2026: A test mail server in the Docker setup whose emails you can read in the browser (Mailpit); your Postfix only for real sending** |
| 6 | 6 | Production web server inside the container | **Decided 6 Nov 2026: waitress (one process, as the design requires)** |

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
6. **Secret values for auditors** (decided 6 Nov 2026): auditors always see
   secret values (PIN, PUK, unlock PIN) on the request page and in the PDF
   report, like IT does; phase 4 still masks them for auditors and is changed
   accordingly. Lists, CSV export, emails and the audit page keep them masked
   (design). The log records that a secret was viewed or printed, never its value.
7. **Retention setting** (default off): when switched on, the scheduler applies
   the agreed rule (decision 4) to requests closed longer than the set
   number of months, and logs each run. It cannot be switched on without the
   number of months.

### Tests

Search filters and permissions (starter only own, auditor read-only but
sees secret values on the request page and in the PDF; viewing is logged), CSV content and encoding, PDF content (pypdf text
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
7. **End-to-end tests**: the test suite in Docker, see "Test suite in Docker" below.
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

## Test suite in Docker (built in phase 6)

One command runs every test on any machine with Docker, with no Python setup:

```
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
```

(also as a short script: `scripts/test.sh` for Linux, `scripts/test.ps1` for Windows).

| Container | Purpose |
| --- | --- |
| `app` | WFTOOL built from the same Dockerfile as production, with a fresh test data folder and test key file |
| `mail` | Mailpit: receives every email; the tests read them over its API (links, attachments, HTML and text) |
| `tests` | Python, pytest and Playwright with Chromium; runs the three parts below against `app` and `mail` |

What runs, in order:

1. **Unit and integration tests** (the existing pytest suite, 114+ tests):
   engine, sections, builder, links, outbox, timers, pages.
2. **End-to-end tests against the running container**, through real SMTP to
   Mailpit and a real browser:
   - build the IT Assets Checklist from an empty workflow in the builder and publish it;
   - IT Assets: start with sections and two monitors, sign from the email link on a phone-size screen, PDF received by user and IT;
   - IT Assets: rejection by the user, change and resend, paper signature with print PDF and scan upload;
   - HR Onboard: manager approves with software, AutoCAD approver, HR group confirm (second HR link closed), IT copy;
   - reminder and expiry with a test clock (the app accepts a time offset only in test mode);
   - search, PDF report and CSV export (phase 5), tamper check reporting a manipulated entry;
   - backup and restore into a second container, tamper check still ok.
3. **Checks on the image**: runs as non-root, key file not inside `/data`,
   security headers (CSP) present, no external requests from the pages.

Results: exit code 0 or 1 for CI, a JUnit XML file and an HTML report with
screenshots of failed steps in `./test-results/`.

Safety: the test mode (test clock, test key) is switched on only by an
environment variable that the production compose file never sets; the app
shows a red "TEST MODE" bar when it is on.

Done when the command above passes on a fresh machine with only Docker installed.

## Wiki articles (written in phase 6)

The GitHub wiki of GFGMNET/WFTOOL, German first with English versions. Each
article is short, task-based, with screenshots from `docs/screenshots/`. The
README keeps only the overview and links to the wiki.

| # | Article | For | Content |
| --- | --- | --- | --- |
| 1 | Start here / Überblick | everyone | What WFTOOL does, roles (admin, starter, auditor), approvers without login, links to all articles |
| 2 | Installation with Docker | IT admin | Server requirements, docker compose, key file outside the data folder, first start, first sign-in |
| 3 | Reverse proxy and HTTPS | IT admin | Example for the proxy in use, forwarded headers, secure cookies |
| 4 | Settings | admin | SMTP (relay without login), test email, company domains, app address, deadlines, holidays |
| 5 | AD and software import | IT admin | PowerShell export script, Task Scheduler, CSV format, safety rules (10 %), import page |
| 6 | Lists | admin | Hardware models and vendors, deactivate instead of delete |
| 7 | Building a workflow | admin | Form designer, sections (conditional, repeatable), steps, who, conditions, email texts, publish, versions |
| 8 | Example: IT Assets Checklist | admin | Step-by-step rebuild of the example, as in the phase 3 acceptance test |
| 9 | Example: HR Onboard | admin | Fields, manager approval, software approval per item, HR confirm, IT copy |
| 10 | Who may start | admin | Groups per workflow, accounts with email |
| 11 | Starting and following a request | starter | Start form, request page, resend, cancel, change and resend after rejection |
| 12 | Approving and signing from an email | approvers, signers | What the email looks like, the link page, reject with reason, signature pad, paper |
| 13 | Paper signature | starter | Print, sign, scan, upload with the request number |
| 14 | Search, PDF and CSV | auditor, admin | Search filters, PDF report, CSV export, secret values (auditors see them; viewing is logged) |
| 15 | Audit and tamper check | auditor, admin | Event log, chain check, daily anchor email, what to do if the check fails |
| 16 | Reminders, expiry and reassigning | admin | Timers, open tasks page, inactive people, outbox |
| 17 | Backup, restore and update | IT admin | Backup command, key file backup kept separately, restore test, update steps |
| 18 | Data protection and retention | admin, data protection officer | What is recorded, works council note (§87(1) no. 6 BetrVG), retention setting, not legal advice |
| 19 | Troubleshooting / FAQ | everyone | Emails not arriving, link no longer works, import rejected, locked account, forgotten admin password |

Done when every article exists in German and English and each admin task in
the app can be done by following one article.

## Order and size (estimate from the design, not a measurement)

| Phase | Lines (est.) | Review stop |
| --- | --- | --- |
| 5. Audit and reports | 800–1,000 | after push |
| 6. Setup and operation | 500–700 plus scripts and docs | after push |
