"""Run the whole suite without Docker: starts the app (test mode) and a stand-in for Mailpit,
then e2e/run.py. Needs Python with requirements.txt, requirements-test.txt and
`playwright install chromium` (or E2E_CHROME=/path/to/chrome)."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from e2e.fake_mailpit import FakeMailpit  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    results = ROOT / "test-results"
    work = results / "local-run"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    mailpit = FakeMailpit()
    port = free_port()
    env = dict(os.environ, WFTOOL_DATA=str(work / "data"), WFTOOL_KEY_FILE=str(work / "keys" / "wftool.key"),
               WFTOOL_TEST_MODE="1", WFTOOL_TEST_ADMIN_PASSWORD="test-admin-password")
    app = subprocess.Popen([sys.executable, "-m", "wftool", "run", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=open(work / "app.log", "w"), stderr=subprocess.STDOUT)
    try:
        time.sleep(1)
        suite_env = dict(os.environ, WFTOOL_E2E_URL=f"http://127.0.0.1:{port}",
                         MAILPIT_URL=f"http://127.0.0.1:{mailpit.http_port}", SMTP_HOST="127.0.0.1",
                         SMTP_PORT=str(mailpit.smtp_port), WFTOOL_E2E_DATA=str(work / "data"),
                         WFTOOL_E2E_ADMIN_PASSWORD="test-admin-password", E2E_RESULTS=str(results))
        return subprocess.call([sys.executable, "-m", "e2e.run"], cwd=ROOT, env=suite_env)
    finally:
        app.terminate()
        app.wait(timeout=10)
        mailpit.close()


if __name__ == "__main__":
    sys.exit(main())
