# CSV import format

Put these files into the app's import folder (`data/import/`). The app checks
the folder every 5 minutes and imports a file only when it changed.

All files: **UTF-8** (with or without BOM), **semicolon** as separator, one
header row with exactly these column names (order does not matter, extra
columns are ignored). In PowerShell: `Export-Csv -Encoding UTF8 -Delimiter ';' -NoTypeInformation`.

## users.csv

| Column | Required | Notes |
| --- | --- | --- |
| samaccountname | no | AD login name, shown for reference |
| email | yes | the key; must be unique |
| displayname | yes | |
| department | no | |
| manager_email | no | email of the manager (AD attribute `manager`, resolved to the manager's mail) |
| enabled | no | `True`/`False` (also yes/no, 1/0, ja/nein); empty = enabled |

Users missing from the file, or with `enabled` = False, become inactive. They
are never deleted, so old requests and reports still show them.

## groups.csv

| Column | Required | Notes |
| --- | --- | --- |
| group | yes | team name, e.g. `HR`, `IT`, `Purchasing` |
| member_email | yes | one row per member; must be an active user from users.csv |

A group missing from the file becomes inactive. Groups are used for "who may
start" a workflow and for Notify and Confirm steps.

## software.csv

| Column | Required | Notes |
| --- | --- | --- |
| software | yes | name shown in the form; must be unique |
| needs_approval | yes | `yes`/`no` (also True/False, 1/0, ja/nein) |
| approver_email | if needs_approval | must be an active user |

An entry that needs approval but has no valid approver is imported as
inactive ("not usable until fixed") and reported.

## Safety rules

- Import order: users, then groups, then software.
- A file that is empty, not UTF-8, missing a column, or that would deactivate
  more than 10 % of the active entries at once is **rejected**; the existing
  data stays unchanged and the admin sees it on the Imports page.
  (The 10 % limit can be changed in Settings.)
- Single bad rows are skipped and listed; the rest is imported.
