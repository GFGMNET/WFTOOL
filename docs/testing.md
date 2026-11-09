# Testing WFTOOL

You need Docker (with Compose). Everything else runs in containers.

The first build downloads from Docker Hub, PyPI and the Debian package servers
(`deb.debian.org`); later runs use the cached images.

## 1. Run every test

```
docker compose -f docker-compose.test.yml up --build --renew-anon-volumes --abort-on-container-exit --exit-code-from tests
```

Or `scripts/test.sh` (Linux) / `scripts\test.ps1` (Windows), which also cleans up afterwards.

- Runs the unit tests, then the end-to-end run in a real browser against a fresh app:
  settings, CSV import, lists, accounts, building IT Assets in the builder, HR Onboard import,
  signing on a phone, rejection and paper signature, HR approvals by email, reminder and expiry,
  search, PDF, CSV, tamper check, backup, security checks.
- Result: exit code 0 = all passed. Report: `test-results/report.html` (screenshots of failures).
- Emails of the run: http://localhost:8025 while it runs.

## 2. Test by hand

```
docker compose -f docker-compose.test.yml --profile manual up --build -d manual mail
```

Open http://localhost:8080 (red TEST MODE bar). Emails: http://localhost:8025.

The setup starts ready to test:

- settings for the test mail server, example people, groups and software (imported from
  `test-data/import/`), hardware lists, **HR Onboard** and **IT Assets Checklist** published,
  who may start: HR Onboard → HR, IT Assets → IT.

| Username | Password | Role | Can start |
| --- | --- | --- | --- |
| `admin` | `test-admin-password` | admin | everything |
| `hanna` | `test-user-password` | starter (HR) | HR Onboard |
| `ina` | `test-user-password` | starter (IT) | IT Assets Checklist |
| `auditor` | `test-user-password` | auditor | – (search, PDF, audit) |

Approvers and signers (e.g. `max.mueller@company.de`, `eva.engel@company.de`) have no account:
they act from the email links shown at http://localhost:8025.

**Test the import:** change or replace the CSV files in `test-data/import/` (templates and a short
instruction: `templates/csv/`), then *Admin → Imports → Import jetzt ausführen*.

Stop: `docker compose -f docker-compose.test.yml --profile manual down`.
**Start again with fresh test data:** stop, delete the folders `test-data/data`, `test-data/keys` and the
CSV files in `test-data/import/`, start again.

## 3. Without Docker (developers)

`python e2e/local.py` with Python 3.11+, `pip install -r requirements.txt -r requirements-test.txt`
and Chrome or Chromium (`E2E_CHROME=/path/to/chrome`, or `playwright install chromium`).
