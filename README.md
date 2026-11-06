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
| 3. Builder | Form designer with sections, workflow builder, publish checks, export/import | **done, waiting for review** |
| 4. Requests and email links | Pages, single-use links, outbox, reminders, signature upload | planned |
| 5. Audit and reports | Tamper check page, daily anchor email, PDF and CSV reports | planned |
| 6. Examples and setup | Docker setup, end-to-end tests | planned |

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

Requires Python 3.11+ with `cryptography` and `Flask` (reportlab follows in phase 5).

```
pip install -r requirements.txt
python -m pytest
```
