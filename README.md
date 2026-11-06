# WFTOOL

Self-hosted workflow builder: design a form, define who approves, signs or is
notified, and publish. Approvers decide from an email link without logging in,
and every decision is recorded with email address, time, IP address and browser.

Licence: MIT. Design: see the design document v2 (Workflow Builder).

## Status

| Phase | Content | State |
| --- | --- | --- |
| 1. Engine | Database, workflow definitions and versions, requests, steps, decisions, audit chain | **done** |
| 2. Master data and admin | CSV import (AD users, groups, software), lists, settings, accounts | planned |
| 3. Builder | Form designer, workflow builder, publish checks in the browser | planned |
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

### Conditions (`when`)

`{"field": "...", "op": "...", "value": ...}` with `equals`, `not_equals`,
`contains`, `not_contains`, `is_set`, `is_empty`, `any_needs_approval`.
A step whose condition is false is skipped and logged.

## Examples

`examples/workflows/` holds HR Onboard and IT Assets Checklist as definition
files; the tests import, publish and run both.

## Development

Requires Python 3.11+ with `cryptography` (Flask, reportlab and PyYAML follow in
later phases).

```
pip install -r requirements.txt
python -m pytest
```
