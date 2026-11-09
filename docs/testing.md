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

1. Open http://localhost:8080, sign in as `admin` / `test-admin-password` (red TEST MODE bar).
2. Settings:
   - SMTP server `mail`, port `1025`, encryption `none`, sender e.g. `wftool@company.de`
   - App address `http://localhost:8080`, admin email, company domain (e.g. `company.de`)
3. Put your CSV files into `test-data/import/` (templates and a short instruction: `templates/csv/`),
   then *Admin → Imports → Import jetzt ausführen*.
4. Every email the app sends: http://localhost:8025.

Stop: `docker compose -f docker-compose.test.yml --profile manual down`.
Start again from empty: stop, then delete `test-data/data` and `test-data/keys`.

## 3. Without Docker (developers)

`python e2e/local.py` with Python 3.11+, `pip install -r requirements.txt -r requirements-test.txt`
and Chrome or Chromium (`E2E_CHROME=/path/to/chrome`, or `playwright install chromium`).
