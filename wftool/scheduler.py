"""The single background thread (design: one scheduler in one app process).

Every 30 seconds it sends due emails from the outbox; every 5 minutes it also
runs the CSV import (unchanged files are skipped) and the timers (expiry,
reminder, notice to the starter). One process means nothing is sent twice.
"""

import logging
import threading

from . import imports, outbox, timers

log = logging.getLogger("wftool.scheduler")


class Scheduler:
    def __init__(self, db, import_dir, *, engine=None, cipher=None, files=None, interval_seconds=300,
                 outbox_seconds=30):
        self.db = db
        self.import_dir = import_dir
        self.engine = engine
        self.cipher = cipher
        self.files = files
        self.interval = interval_seconds
        self.outbox_interval = outbox_seconds
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="wftool-scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def run_once(self):
        """Imports and timers, then the outbox."""
        try:
            for r in imports.run_folder(self.db, self.import_dir):
                if r["status"] != "unchanged":
                    log.info("import %s: %s %s", r["file"], r["status"], r["message"] or "")
        except Exception:  # never let one bad run stop the thread
            log.exception("import run failed")
        if self.engine is not None:
            try:
                result = timers.run(self.db, self.engine)
                if any(result.values()):
                    log.info("timers: %s", result)
            except Exception:
                log.exception("timers failed")
        self.send_mail()

    def send_mail(self):
        if self.cipher is None or self.files is None:
            return
        try:
            result = outbox.send_due(self.db, self.cipher, self.files)
            if result["sent"] or result["retry"] or result["failed"]:
                log.info("outbox: %s", result)
        except Exception:
            log.exception("outbox failed")

    def _run(self):
        elapsed = self.interval  # run everything at start
        while not self._stop.is_set():
            if elapsed >= self.interval:
                self.run_once()
                elapsed = 0
            else:
                self.send_mail()
            self._stop.wait(self.outbox_interval)
            elapsed += self.outbox_interval
