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
| 5 | 6 | Mail server for testing | **Decided 6 Nov 2026: A test mail server in the Docker setup whose emails you can read in the browser (Mailpit); production uses the Exchange relay** |
| 6 | 6 | Production web server inside the container | **Decided 6 Nov 2026: waitress (one process, as the design requires)** |

**What I will do:** phase 5, review stop, phase 6, review stop. Details below.

**Known about the production environment** (owner, 6 Nov 2026):
- The app runs locally (on-premises, in the company network only; no internet access needed).
- Production email goes through an **Exchange relay** (receive connector that allows the app server's IP, no login).
- AD groups are named as in the examples (HR, IT, Purchasing …).
- Backups are done by the owner; the wiki says what to back up (data folder, key file separately).
- **HTTP, not HTTPS** (owner decision, 6 Nov 2026), because the app is only reachable inside the
  company network. Accepted risk: sign-in passwords, session cookies, email link codes and the
  secret values shown to signers travel unencrypted inside the network. No certificate and no
  reverse proxy are needed; the app is reached directly, e.g. `http://wftool.company.local:8080`.
  HTTPS can be added later without changes to the app (reverse proxy plus `WFTOOL_SECURE_COOKIE=1`).

**What I need from you:** nothing for phases 5 and 6.

- The app runs in a container, so the guide is written for Docker and does not depend on the host OS.
- The values of your environment are entered by you at go-live, not needed for the build:
  the app address and the Exchange relay go into Settings; the users' OU is a parameter at the
  top of the PowerShell export script. The go-live table below lists them.
- Your master data goes into the CSV files described in `examples/csv/README.md`
  (`users.csv`, `groups.csv`, `software.csv`); hardware models and vendors are entered under Lists.

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
   `http://server:8025`. For production, Settings point to the Exchange relay instead.
3. **Direct HTTP access** (no reverse proxy, owner decision): the app listens on
   its own port. Because no proxy sits in front, the app must **not** trust
   `X-Forwarded-For` headers (today it does, for a proxy): otherwise a person
   could fake the IP address recorded with a decision. New setting
   `WFTOOL_BEHIND_PROXY` (default off) switches the proxy handling on only if
   HTTPS with a proxy is added later. A test proves a faked header is ignored.
4. **Backup support and update** (backups themselves are done by the owner):
   - `python -m wftool backup <file>`: consistent SQLite copy while the app runs, for your backup job to pick up;
   - wiki: what to back up (`/data` and the key file, separately) and how to test a restore;
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
| 3 | Network access (HTTP) | IT admin | Port and firewall inside the network, DNS name, why HTTP was chosen and its accepted risk, how to add HTTPS later if wanted |
| 4 | Settings | admin | SMTP (relay without login), test email, company domains, app address, deadlines, holidays |
| 5 | AD and software import | IT admin | PowerShell export script, Task Scheduler, CSV format, safety rules (10 %), import page |
| 5a | AD groups and workflows | admin | Groups named after the team (HR, IT, Purchasing) as in the examples; adding a group to the export script; tying a group to a workflow in three places: *Who may start*, a *Confirm* step (first member confirms for all) and a *Notify* step; approvals always go to one person, never a group |
| 6 | Lists | admin | Hardware models and vendors, deactivate instead of delete |
| 7 | Building a workflow | admin | Form designer, sections (conditional, repeatable), steps, who, conditions, email texts, publish, versions |
| 8 | Example: IT Assets Checklist | admin | Step-by-step rebuild of the example, as in the phase 3 acceptance test |
| 9 | Example: HR Onboard | admin | Fields, manager approval, software approval per item, HR confirm, IT copy |
| 10 | Who may start | admin | Groups per workflow, accounts with email (see 5a) |
| 10a | Accounts and roles | admin | Creating accounts, the AD email on each account, roles admin/starter/auditor, password rules, locked accounts |
| 11 | Starting and following a request | starter | Start form, request page, resend, cancel, change and resend after rejection |
| 12 | Approving and signing from an email | approvers, signers | What the email looks like, the link page, reject with reason, signature pad, paper |
| 13 | Paper signature | starter | Print, sign, scan, upload with the request number |
| 14 | Search, PDF and CSV | auditor, admin | Search filters, PDF report, CSV export, secret values (auditors see them; viewing is logged) |
| 15 | Audit and tamper check | auditor, admin | Event log, chain check, daily anchor email, what to do if the check fails |
| 16 | Reminders, expiry and reassigning | admin | Timers, open tasks page, inactive people, outbox |
| 17 | Backup, restore and update | IT admin | What to back up (data folder, key file separately), backup command, restore test, update steps |
| 17a | Key file and encryption | IT admin | Where the key file lives, why it is kept apart from the data, what is lost without it (secret values, signatures, scans), never reuse the test key |
| 17b | Exchange relay | IT admin | Receive connector allowing the app server's IP without login, sender address, SPF/DKIM so emails are not marked as spam, test email from Settings |
| 17d | Moving workflows from test to production | admin | Export on the test system, import on production, check, publish; never copy the test database |
| 17e | Health and daily checks | admin | Health page, warnings on the start page (failed emails, inactive people, rejected imports), outbox |
| 18 | Data protection and retention | admin, data protection officer | What is recorded, works council note (§87(1) no. 6 BetrVG), retention setting, not legal advice |
| 19 | Troubleshooting / FAQ | everyone | Emails not arriving, link no longer works, import rejected, locked account, forgotten admin password |

Done when every article exists in German and English and each admin task in
the app can be done by following one article.

## Going live: test → production (wiki article "Going live", written in phase 6)

| Area | Test | Production |
| --- | --- | --- |
| App address (Settings) | localhost | real address in the network, e.g. `http://wftool.company.local:8080`; every email link is built from it |
| Email (Settings) | Mailpit | Exchange relay: host, port, STARTTLS yes/no, no login, sender address |
| HTTP | direct | direct on its port (no proxy, `WFTOOL_BEHIND_PROXY` off); links work only inside the company network |
| Key file | test key | new key, outside the data folder, backed up separately |
| Database | test data | fresh start; workflows come over by export/import (17d) |
| Admin | admin/admin | new password, admin email set |
| Accounts | test users | real HR/IT accounts, each with its AD email |
| AD import | example CSV files | PowerShell script, service account, Task Scheduler, import folder |
| Company domain, days off | example | your domain(s), extra days off |
| Who may start | test groups | AD groups per workflow (HR, IT …) |
| Test mode | may be on | off (no red "TEST MODE" bar) |
| Backup | none | owner's backup job: data folder and key file separately; one restore test |
| Data protection | – | works council and data protection officer agreed, retention setting chosen |

## Order and size (estimate from the design, not a measurement)

| Phase | Lines (est.) | Review stop |
| --- | --- | --- |
| 5. Audit and reports | 800–1,000 | after push |
| 6. Setup and operation | 500–700 plus scripts and docs | after push |
