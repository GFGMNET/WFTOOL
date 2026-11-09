# WFTOOL

Self-hosted workflow builder: design a form, define who approves, signs or is
notified, and publish. Approvers decide from an email link without logging in,
and every decision is recorded with email address, time, IP address and browser.

Licence: MIT. Design: see the design document v2 (Workflow Builder).

## Status

| Phase | Content | State |
| --- | --- | --- |
| 1. Engine | Database, workflow definitions and versions, requests, steps, decisions, audit chain | **done** |
| 2. Master data and admin | CSV import (AD users, groups, software), lists, settings, accounts | **done** |
| 3. Builder | Form designer with sections, workflow builder, publish checks, export/import | **done** |
| 4. Requests and email links | Request pages, single-use links, HTML emails, outbox, reminders, signatures | **done** |
| 5. Audit and reports | Search, CSV, full PDF report, audit page, daily anchor email, retention | **done** |
| 6. Test setup | Docker image, test suite with one command, CSV templates, test import folder, health page, backup command | **done, waiting for review** |

## Phase 1: what the engine does

- **Database** (`wftool/db.py`): one SQLite file in WAL mode with numbered
  migrations. All tables of the design are created now, so later phases only add.
- **Workflow definitions** (`wftool/definitions.py`): a workflow is JSON with
  `fields` and ordered `steps`. `validate()` checks the structure;
  `publish_problems()` also checks master data (groups exist, every list item
  that needs approval has an active approver, …).
- **Versions** (`wftool/workflows.py`): drafts are saved freely; publishing
  stores an immutable version. Running requests keep their version.
- **Engine** (`wftool/engine.py`): start, conditions, recipients, decisions,
  rejection (back to the starter or one step back), resubmit with a new form
  version, cancel, reassign, signature on screen and on paper.
- **Audit** (`wftool/audit.py`): append-only event log; each entry's hash
  covers the previous one. `verify()` reports the first broken entry;
  `latest_hash()` is the value phase 5 emails daily as the anchor.
- **Secrets** (`wftool/crypto.py`): secret fields (PIN, PUK) are encrypted with
  a key file kept outside the data folder; they never appear in the audit log.
- **Notifier** (`wftool/notify.py`): the interface the engine calls when an
  email is needed. Phase 4 implements it with links and the outbox.

### Step types

| Type | Who | Done when |
| --- | --- | --- |
| `fill` | one person | they submit the step's fields |
| `approve` | one person | they approve or reject (with reason) |
| `fill_approve` | one person | they fill the fields and approve, or reject |
| `approve_per_item` | approver of each selected list item that needs approval | every item approved; one rejection rejects the request |
| `sign` | one person | they sign on screen, or on paper (scan uploaded by the starter) |
| `confirm` | one person or a group | someone presses the button; for a group the first one counts |
| `notify` | people, groups or an email field | email sent, continues at once |

Approvals and signatures always go to one person, never a group. The starter
never approves their own request: such a task goes to the admin email. A
recipient that can't be resolved (empty field, inactive user, item without
approver) also goes to the admin, and the event log says why.

### Recipient kinds (`who`)

`person` (fixed email), `group`, `field` (a person or email field of the form),
`starter`, `manager_of` (manager of a person field, or of the starter),
`item_approver` (only in `approve_per_item`).

### No response (`on_timeout`)

When a task's link expires (14 days by default), `wait` (default) keeps the
step open and tells the starter and the admin, who can resend or reassign;
`reject` rejects the task automatically with the reason "No response within
14 days", and the request goes where the step's `on_reject` says. Only
approval and sign steps can use `reject`; paper signatures never expire.

### Conditions (`when`)

`{"field": "...", "op": "...", "value": ...}` with `equals`, `not_equals`,
`contains`, `not_contains`, `is_set`, `is_empty`, `any_needs_approval`.
A step whose condition is false is skipped and logged.

## Phase 6: test setup

How to test: [docs/testing.md](docs/testing.md). The go-live compose file and the wiki follow after the final approval
([docs/phase5-6-plan.md](docs/phase5-6-plan.md), "Process from now to go-live").

- **Docker image** (`Dockerfile`): app and background tasks in one process (waitress), runs as an
  unprivileged user, data in `/data`, key file in `/keys` (separate volume).
- **Test suite with one command** (`docker-compose.test.yml`): unit tests, then a full end-to-end run in a
  real browser against a fresh app, with Mailpit as the test mail server. Report in `test-results/`.
- **Hands-on testing**: the `manual` profile keeps its data in `test-data/`; your CSV files go into
  `test-data/import/`. Templates: `templates/csv/`.
- **Test mode** (`WFTOOL_TEST_MODE=1`, test setup only): red bar on every page, test clock and helpers
  for the automated run. Never set in production.
- **Proxy headers** are ignored unless `WFTOOL_BEHIND_PROXY=1`, so the recorded IP address cannot be faked.
- **Backup command**: `python -m wftool backup <file>` (consistent copy while the app runs).
- **Admin → Health**: last import, emails waiting or failed, daily fingerprint email, open tasks after the
  reminder, free disk space.

## Phase 5: audit and reports

Plan and decisions: [docs/phase5-6-plan.md](docs/phase5-6-plan.md).

- **Search** (*Requests*): by start date, workflow, person (starter or anyone who
  acted, by name or email), status and request number; 50 per page. Starters
  see only their own requests; admins and auditors see all.
- **CSV export** of the search result (`wftool/reports.py`): semicolon, UTF-8 for
  Excel, one row per request with its decisions; never contains form values.
- **PDF report** (*PDF* on the request page or in the list, `pdf.report_pdf`):
  the data, every step with person, time, IP address and browser, the signature,
  the paper scan as extra pages with its fingerprint, decisions of earlier rounds,
  earlier form versions with what changed, the history ("link opened" listed
  apart, never as proof), and the result of the tamper check.
- **Audit** page (admin and auditor): the event log with filters, *Check the event
  log now*, and the list of daily fingerprint emails with "still matches".
- **Daily fingerprint email** to the admin email at 06:00 (Settings → Audit and
  retention): the newest fingerprint of the log, so removing the newest entries
  is noticed too.
- **Secret values for auditors** (owner decision): auditors always see PIN, PUK
  and unlock PIN on the request page and in the PDF; each viewing is logged.
  Lists, CSV, emails and the audit page keep them masked.
- **Retention** (Settings, empty = off): once a day, IP address and browser are
  removed from requests closed more than the set months ago; decisions stay.
  The event log keeps salted fingerprints of IP address and browser, so the
  tamper check still passes after removal (`wftool/audit.py`).

## Phase 4: requests, email links and emails

Decisions for this phase: [docs/phase4-brief.md](docs/phase4-brief.md).

- **Start a request:** *Requests* (or the start page) lists the workflows you may
  start. The form shows a section when its box is ticked and adds rows with
  "+ Add another …"; secret fields have a *Show* button. Your account needs an
  email address: requests run under it.
- **Email links** (`wftool/links.py`): every task gets a personal link
  `…/t/<code>` (256-bit code; only its SHA-256 hash is stored), valid 14 days by
  default. Opening it shows the request and the buttons and logs "link
  fetched"; only pressing a button decides. Then email, time, IP address and
  browser are recorded and the link is used up. A used, expired, replaced or
  closed link explains why and whom to contact. People with an account whose
  email is the task's can also open the task from their start page.
- **Task pages** for every step type: fill, approve or reject (reason
  required), fill and approve, approve per item, sign (signature pad for
  mouse, finger or pen; or "sign on paper"), confirm. German by default with a
  DE/EN switch. The signer sees the secret values; approvers see them masked.
- **Request page:** steps as a timeline with person, decision, time and IP,
  the data (secrets masked; *Show secret values* is logged), the history, and
  the actions: send the link again, sign on paper instead, print the page for
  signing, upload the scan (PDF/JPG/PNG up to 20 MB; the starter types the
  request number printed on the page), cancel with reason, change and send
  again after a rejection, reassign (admin), download the PDF.
- **Emails** (`wftool/mailer.py`, `wftool/emails.py`): HTML with a plain-text
  copy, a button for the link and the request's details (secrets masked).
  Approvers and signers get German; the starter gets their account language;
  Notify steps use the default email language. Notify "with PDF" attaches a
  simple PDF of the request (`wftool/pdf.py`; phase 5 extends it).
- **Outbox** (`wftool/outbox.py`): emails are queued in the same transaction as
  the change and sent every 30 seconds by the background task, retried for
  about a day (1, 5, 15, 60 minutes …). Admin → *Outbox* shows them, can send
  now or retry. PDFs waiting to be sent are stored encrypted and deleted once sent.
- **Timers** (`wftool/timers.py`, every 5 minutes): one reminder after 5 working
  days with a new link (a paper signature reminds the starter instead); the
  starter is told after 10 working days; an expired link runs the step's
  *On no response* (wait: tell starter and admin; or reject). Working days
  follow Saxony's holidays plus the extra days in Settings.
- **Admin → Open tasks:** all open tasks; tasks of people no longer active in
  AD are marked and can be reassigned. The start page warns about them and
  about emails that could not be sent.
- **Stored files** (`wftool/files.py`): signature images and scans are
  encrypted with the key file, in `data/files/`.

Screenshots: [docs/screenshots/](docs/screenshots/).

## Phase 3: form designer and workflow builder

Admin → **Workflows**. A workflow is created empty (or imported from a file),
built in the browser and published. Brief: [docs/phase3-brief.md](docs/phase3-brief.md).

- **Form designer** (tab *Form*): add fields from the palette; drag them by the
  ⠿ handle or move them with the ↑/↓ buttons (keyboard users; ↑/↓ also moves a
  field into or out of a section). A card opens to edit label DE/EN, id, help
  text, required, default, list or fixed options, min/max, max length and
  *Filled by* (at the start, or a Fill / Fill-and-approve / Sign step). Ids
  follow the label until edited; renaming an id updates every reference
  (steps, conditions, sections, email placeholders).
- **Live preview** beside the list (below it on phones): the form as the user
  will see it, per stage (start or a later fill step) and in DE or EN. Ticking a
  box shows its section; repeatable sections add and remove rows.
- **Workflow builder** (tab *Steps*): ordered steps, each with type, who
  (fixed person, AD group for Notify/Confirm only, person/email field, the
  starter, manager of, item approver), condition, on reject, on no response
  (approval and sign steps), paper signature (sign), PDF attachment (notify)
  and email subject and text DE/EN. Empty email texts use the defaults per step
  type (`wftool/emails.py`); placeholders: `{request_no}`, `{workflow}`,
  `{step}`, `{starter}`, `{link}` and any field id such as `{employee}`.
  Secret fields and fields in repeatable sections can't be placeholders.
- **Check and publish:** the page checks the draft on the server as you edit
  (nothing is saved) and shows each problem in plain language on the field or
  step it concerns; the *Check* tab lists them all. *Save draft* stores the
  draft; *Publish* saves and publishes a new version if there are no problems.
  Running requests keep their version. If another admin saved in between, the
  save is refused instead of overwriting. *Discard unpublished changes* goes
  back to the last version.
- **Export / import:** *Export* downloads the draft as JSON; *Import workflow*
  (on the Workflows page) loads such a file as the draft of the workflow with
  the same name (or a new one). Nothing is published by an import.
- **Advanced:** the draft as JSON can be edited directly on the builder page;
  this also works without JavaScript.
- Every save, rename, import, discard and publish is an audit event.
  The designer is `wftool/web/static/builder.js` (no libraries, no build step,
  CSP `default-src 'self'`).

Screenshots (desktop and phone): [docs/screenshots/](docs/screenshots/).

### Sections

```json
{"id": "laptop_issued", "type": "checkbox", "label": {"de": "Laptop ausgegeben", "en": "Laptop issued"}},
{"id": "laptop", "type": "section", "label": "Laptop",
 "show_when": {"field": "laptop_issued", "op": "equals", "value": true}},
{"id": "laptop_model", "type": "dropdown", "section": "laptop", "list": "laptop_models", "label": "Modell", "required": true},
{"id": "monitors", "type": "section", "label": "Monitor",
 "show_when": {"field": "monitor_issued", "op": "equals", "value": true},
 "repeat": {"min": 1, "max": 6, "add_label": {"de": "Weiteren Monitor hinzufügen", "en": "Add another monitor"}}}
```

- A field belongs to a section by its `section` key; no sections inside
  sections, no signature in a section. All fields of a section are filled by
  the same step, and the section belongs to that step.
- `show_when` uses a field outside sections that is filled at or before the
  section's step. A hidden section's fields are not required and not stored;
  a version saved later without them drops them (earlier versions keep them).
- A normal section's values are stored flat (`laptop_model`). A repeatable
  section is a list of rows under its id
  (`"monitors": [{"monitor_model": "P2423", "monitor_serial": "M1"}]`), with
  min/max count; rows left completely empty are ignored. Secret fields inside
  rows are encrypted like any other secret.
- Fields in repeatable sections cannot be used by steps (recipient, condition,
  approve per item) or as email placeholders.
- Every problem is a `definitions.Problem`: a string that also knows the field
  or step it concerns (`kind`, `index`, `ref`) and a translatable template.

## Phase 2: master data and admin

- **Start:** `python -m wftool run` (app on port 8080 plus the background
  scheduler). First sign-in: **admin / admin**; the app forces a new password
  at once. `python -m wftool import` runs the CSV import once.
- **Folders:** `WFTOOL_DATA` (default `./data`: database, `import/` folder,
  session key) and `WFTOOL_KEY_FILE` (default `./keys/wftool.key`). Keep the key
  file outside the data folder and back it up separately.
- **CSV import** (`wftool/imports.py`): `users.csv`, `groups.csv`, `software.csv`
  in `data/import/`, checked every 5 minutes; unchanged files are skipped.
  Format and safety rules: [examples/csv/README.md](examples/csv/README.md).
- **Accounts** (`wftool/accounts.py`): roles admin, starter, auditor; passwords
  at least 12 characters; 5 wrong attempts lock the account for 15 minutes;
  sign-out after 60 minutes without activity.
- **Who may start:** per workflow, one or more AD groups. A starter account's
  email must belong to an active AD user in one of them. Admins may start all.
- **Lists** (`wftool/applists.py`): hardware models, vendors and other
  dropdowns, edited in the app; entries are deactivated, never deleted.
- **Settings** (`wftool/settings.py`): SMTP (relay without login supported:
  leave user and password empty), test email, company domains, app address,
  admin email, deadlines, Saxony public holidays plus extra days off.
- **Admin pages** (`wftool/web/`): German and English, CSRF-protected forms,
  no external resources (no Google Fonts, no CDN).

## Examples

`examples/workflows/` holds HR Onboard and IT Assets Checklist as definition
files; the tests import, publish and run both. They can be imported in the
app with *Import workflow*.

The IT Assets Checklist has the sections Laptop, Docking station, Monitor
(repeatable, 1–6) and Smartphone (IMEI, PIN, PUK and unlock PIN; the last three
are secret), each switched on by its own checkbox. It needs these lists under
Admin → Lists: `laptop_models`, `docking_models`, `monitor_models`,
`smartphone_models` and `hardware_vendors`.

## Development

Requires Python 3.11+ and the packages in `requirements.txt` (tests: `requirements-test.txt`).

```
pip install -r requirements.txt
python -m pytest
```
