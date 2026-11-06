# Phase 4 brief: requests, email links and outbox

State: done (6 Nov 2026), waiting for review.

Decided by the owner on 6 Nov 2026 (in addition to the design document v2):

1. Notify steps "with PDF" attach a simple PDF of the request already in phase 4;
   phase 5 extends it into the full report (audit part, tamper check).
2. One reminder per task, after the reminder delay (default 5 working days).
3. Emails are HTML, each with a plain-text copy (multipart/alternative).

## What phase 4 delivers

- Starting a request from a published workflow (form with conditional and
  repeatable sections), the list of requests and the request page.
- Single-use email links (256-bit code, only its hash stored), bound to one task
  and one email address, valid 14 days by default. Opening a link never decides;
  pressing the button does, and records email, time, IP address and browser.
- Link pages for every step type: fill, approve, fill and approve, approve per
  item, sign (on screen, or choose paper), confirm. German by default, DE/EN switch.
- Signed in users can also do their own tasks in the app.
- Outbox: emails are queued in the same transaction as the state change and sent
  by the background thread, with retries.
- Reminder (once), notice to the starter, link expiry (wait or reject per step),
  resend, reassign (admin), cancel (starter or admin).
- Paper signature: print PDF, scan upload (PDF, JPG, PNG, max 20 MB) by the
  starter, who confirms the request number printed on the page.
- Signatures and scans are stored encrypted in the data folder.

**Stop after phase 4** and hand back for review.
