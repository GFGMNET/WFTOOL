"""The single background thread (design: one scheduler in one app process).

Phase 2 runs the CSV import every 5 minutes; files that did not change are
skipped, so this is cheap. Phase 4 adds the outbox, reminders and expiry.
"""

import logging
import threading

from . import imports

log = logging.getLogger("wftool.scheduler")


class Scheduler:
    def __init__(self, db, import_dir, *, interval_seconds=300):
        self.db = db
        self.import_dir = import_dir
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="wftool-scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def run_once(self):
        try:
            for r in imports.run_folder(self.db, self.import_dir):
                if r["status"] != "unchanged":
                    log.info("import %s: %s %s", r["file"], r["status"], r["message"] or "")
        except Exception:  # never let one bad run stop the thread
            log.exception("import run failed")

    def _run(self):
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval)
