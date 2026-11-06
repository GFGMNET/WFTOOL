"""Run every test: the unit tests, then the end-to-end suite. Exit code 0 only if all pass.

Results go to test-results/: junit-unit.xml, junit-e2e.xml, report.html (with screenshots of failures).
"""

import os
import subprocess
import sys

from e2e.helpers import RESULTS


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, E2E_RESULTS=str(RESULTS))
    common = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
    print("== unit and integration tests ==", flush=True)
    unit = subprocess.call(common + ["tests", f"--junitxml={RESULTS / 'junit-unit.xml'}"], env=env)
    print("== end-to-end tests against the running app ==", flush=True)
    e2e = subprocess.call(common + ["-x", "e2e", f"--junitxml={RESULTS / 'junit-e2e.xml'}"], env=env)
    print(f"\nunit: {'passed' if unit == 0 else 'FAILED'} · end-to-end: {'passed' if e2e == 0 else 'FAILED'}"
          f" · report: {RESULTS / 'report.html'}", flush=True)
    return 0 if unit == 0 and e2e == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
