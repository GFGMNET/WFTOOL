# Phase 3 brief: form designer and workflow builder

Read this first, then `README.md`, then the design document v2
("Workflow Builder – Design Document v2", https://claude.ai/artifact/U4c3Q4tkALWqKtbYRfjj7u).
Phases 1 and 2 are done and pushed; all tests pass (`python -m pytest`).

**Stop after phase 3**: push it to `main`, then hand back to the owner for review.
Do not start phase 4 (request pages, email links, outbox).

## Where things stand

| Phase | State |
| --- | --- |
| 1. Engine (`wftool/engine.py`, `definitions.py`, `workflows.py`, `audit.py`) | done |
| 2. Master data, accounts, settings, admin pages (`imports.py`, `accounts.py`, `settings.py`, `applists.py`, `wftool/web/`) | done |
| **3. Builder** | **done (6 Nov 2026), waiting for review** |
| 4. Request pages and email links, outbox, reminders, expiry, paper-scan upload | later |
| 5. Audit page, daily anchor email, PDF and CSV reports | later |
| 6. Docker setup, production server, end-to-end tests | later |

## What phase 3 delivers

1. **Form designer** (admin, in the browser): add fields from a palette, reorder
   them, edit properties: label DE/EN, required, help text, default, list or
   fixed options, min/max, max length, and which step fills the field.
   A live preview shows the form as a user will see it.
2. **Conditional sections** (new, decided 6 Nov 2026): a section is shown only
   when a condition holds, typically a checkbox ("Laptop issued").
   Fields in a hidden section are not required and not stored.
3. **Repeatable sections** (new, decided 6 Nov 2026): a section can be added
   several times ("+ Add another monitor"), with optional min/max count.
4. **Workflow builder**: ordered steps; per step type, who (person, AD group
   only for Notify/Confirm, form field, starter, manager of, item approver),
   condition, on reject (starter / one step back), on no response
   (wait / reject, approval and sign steps only), allow paper (sign steps),
   attach PDF (notify), email subject and text DE/EN with placeholders
   such as `{employee}`, `{request_no}`, `{link}` and defaults per step type.
5. **Draft, check and publish**: save drafts freely; show every problem from
   `definitions.publish_problems()` next to the field or step it concerns;
   Publish creates a new version (running requests keep theirs).
6. **Export / import** of a workflow as a JSON file (already in
   `workflows.export_workflow` / `import_workflow`), with buttons in the UI.
7. **"Who may start"** stays on its own page; link to it from the builder.
8. **Rebuild the IT Assets Checklist example** with sections Laptop, Docking
   station, Monitor (repeatable), Smartphone (with IMEI, PIN, PUK, unlock PIN as
   secret fields), each switched on by a checkbox; one app list per device type
   (`laptop_models`, `docking_models`, `monitor_models`, `smartphone_models`)
   plus `hardware_vendors`. Keep HR Onboard working unchanged.

## Proposed definition format for sections (refine if needed, keep it simple)

```json
{"id": "laptop_issued", "type": "checkbox", "label": {"de": "Laptop", "en": "Laptop"}},
{"id": "laptop", "type": "section", "label": {"de": "Laptop", "en": "Laptop"},
 "show_when": {"field": "laptop_issued", "op": "equals", "value": true}},
{"id": "laptop_model", "type": "dropdown", "section": "laptop", "list": "laptop_models", "label": "Modell", "required": true},
{"id": "monitors", "type": "section", "label": "Monitor", "repeat": {"min": 1, "max": 6, "add_label": {"de": "Weiteren Monitor hinzufügen", "en": "Add another monitor"}},
 "show_when": {"field": "monitor_issued", "op": "equals", "value": true}},
{"id": "monitor_model", "type": "dropdown", "section": "monitors", "list": "monitor_models", "label": "Modell"}
```

- Stored form data: a normal section's fields stay flat (`laptop_model`);
  a repeatable section is stored as a list of objects under its id
  (`"monitors": [{"monitor_model": "...", "monitor_serial": "..."}]`).
- `show_when` may only refer to fields outside sections filled at or before the
  same step. Step conditions (`when`) may not refer to fields inside repeatable
  sections.
- Secret fields inside repeatable sections must be encrypted too (extend
  `Engine._encrypt` / `_decrypt`, which today only handle top-level values).
- Engine changes: `definitions.validate()` (section rules), `Engine._clean_values`
  (skip hidden sections, validate each repeat row, min/max count),
  `start_fields` / `step_fields` (sections belong to the step of their fields).
- The existing `heading` type can stay as a simple visual divider.

## Rules to keep (decided by the owner)

- Approvals and signatures always go to one person, never a group; groups only
  for Notify and Confirm (whoever acts first confirms for the group).
- The starter never approves their own request (goes to the admin email).
- Rejection goes back to the starter by default; resubmit runs all steps again.
- Steps run in a straight order with conditions; no freeform flowcharts in v1.
- Paper signature allowed by default on sign steps.
- On no response: wait (default) or reject automatically, per step.
- German and English UI; emails to approvers and signers in German.
- No external resources: no CDN, no Google Fonts (GDPR), CSP `default-src 'self'`
  (so no inline `<script>` or `style=`; put JS in `wftool/web/static/`).
- **No package installs** in the build environment (network is off): build drag
  and drop yourself (HTML5 drag events plus up/down buttons for keyboard users),
  no SortableJS.
- Every admin change is an audit event; never log secret values.
- Visual style: see `wftool/web/static/app.css` (form-paper grey, ink text,
  stamp-blue actions, rubber-stamp status marks, system fonts). Reuse it.

## Done when

- An admin can build the IT Assets Checklist with all four device sections in
  the browser, from an empty workflow, and publish it.
- Publish problems are shown in plain language next to the right field or step.
- Tests cover: section validation, hidden-section fields not required or
  stored, repeat min/max, secret fields in repeat rows encrypted, the builder
  pages (create, edit, reorder, publish, export/import), and the rebuilt example.
- Screenshots of the designer and builder checked at desktop and phone width.
- README updated; commit pushed to `main` of GFGMNET/WFTOOL.
