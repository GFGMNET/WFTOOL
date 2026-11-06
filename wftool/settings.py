"""Settings edited by the admin, the SMTP test, and the working-day calendar.

SMTP works with a relay that allows the server by IP address: leave user and
password empty and the app sends without logging in.
"""

import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import audit, clock, masterdata
from .errors import ValidationError


@dataclass
class Setting:
    key: str
    kind: str  # text, int, choice, email, url, domains, secret, dates
    group: str
    default: str = ""
    required: bool = False
    choices: tuple = ()
    low: int = None
    high: int = None
    label: dict = field(default_factory=dict)


SETTINGS = [
    Setting("smtp_host", "text", "smtp", required=True,
            label={"de": "SMTP-Server", "en": "SMTP server"}),
    Setting("smtp_port", "int", "smtp", "25", low=1, high=65535, label={"de": "Port", "en": "Port"}),
    Setting("smtp_security", "choice", "smtp", "none", choices=("none", "starttls", "tls"),
            label={"de": "Verschlüsselung", "en": "Encryption"}),
    Setting("smtp_tls_verify", "choice", "smtp", "yes", choices=("yes", "no"),
            label={"de": "Zertifikat prüfen", "en": "Verify certificate"}),
    Setting("smtp_user", "text", "smtp",
            label={"de": "Benutzer (leer bei Relay)", "en": "User (empty for a relay)"}),
    Setting("smtp_password", "secret", "smtp",
            label={"de": "Passwort (leer bei Relay)", "en": "Password (empty for a relay)"}),
    Setting("smtp_sender", "email", "smtp", required=True,
            label={"de": "Absenderadresse", "en": "Sender address"}),
    Setting("company_domains", "domains", "general", required=True,
            label={"de": "Firmen-Domain(s)", "en": "Company domain(s)"}),
    Setting("base_url", "url", "general", required=True,
            label={"de": "Adresse der App (für Links)", "en": "App address (for links)"}),
    Setting("admin_email", "email", "general", required=True,
            label={"de": "Admin-E-Mail", "en": "Admin email"}),
    Setting("default_email_language", "choice", "general", "de", choices=("de", "en"),
            label={"de": "Sprache der E-Mails", "en": "Email language"}),
    Setting("link_validity_days", "int", "deadlines", "14", low=1, high=365,
            label={"de": "Links gültig (Tage)", "en": "Links valid (days)"}),
    Setting("reminder_working_days", "int", "deadlines", "5", low=1, high=60,
            label={"de": "Erinnerung nach (Arbeitstagen)", "en": "Reminder after (working days)"}),
    Setting("starter_notice_working_days", "int", "deadlines", "10", low=1, high=120,
            label={"de": "Hinweis an Antragsteller nach (Arbeitstagen)",
                   "en": "Notice to starter after (working days)"}),
    Setting("extra_holidays", "dates", "deadlines",
            label={"de": "Weitere freie Tage (JJJJ-MM-TT, je Zeile)",
                   "en": "Extra days off (YYYY-MM-DD, one per line)"}),
    Setting("anchor_time", "time", "audit", "06:00",
            label={"de": "Tägliche Prüfwert-E-Mail an den Admin (Uhrzeit)",
                   "en": "Daily fingerprint email to the admin (time)"}),
    Setting("retention_months", "int", "audit", "", low=1, high=600,
            label={"de": "Aufbewahrung: IP-Adresse und Browser entfernen nach (Monaten, leer = aus)",
                   "en": "Retention: remove IP address and browser after (months, empty = off)"}),
    Setting("import_max_deactivate_percent", "int", "import", "10", low=1, high=100,
            label={"de": "Import: max. Deaktivierung auf einmal (%)",
                   "en": "Import: max. deactivation at once (%)"}),
]
BY_KEY = {s.key: s for s in SETTINGS}


def current(db):
    """All settings as {key: value}; secrets are returned as '' or '(set)'."""
    out = {}
    for s in SETTINGS:
        value = masterdata.get_setting(db, s.key, s.default)
        out[s.key] = ("(set)" if value else "") if s.kind == "secret" else (value or "")
    return out


def missing_required(db):
    return [s.key for s in SETTINGS if s.required and not (masterdata.get_setting(db, s.key, "") or "").strip()]


def save(db, cipher, values, *, by, clear_secrets=()):
    """Check and store the submitted settings. Secret fields left empty keep their value."""
    problems, clean = [], {}
    for key, raw in values.items():
        s = BY_KEY.get(key)
        if s is None:
            continue
        raw = (raw or "").strip() if s.kind != "secret" else (raw or "")
        if s.kind == "secret":
            if raw:
                clean[key] = cipher.encrypt_text(raw)
            continue
        try:
            clean[key] = _check(s, raw)
        except ValueError as exc:
            problems.append(f"{key}: {exc}")
    for key in clear_secrets:
        if key in BY_KEY and BY_KEY[key].kind == "secret":
            clean[key] = ""
    if problems:
        raise ValidationError("Please correct the settings.", details=problems)

    changed = {}
    for key, value in clean.items():
        old = masterdata.get_setting(db, key, BY_KEY[key].default)
        if value != (old or ""):
            changed[key] = "(changed)" if BY_KEY[key].kind == "secret" else value
    if not changed:
        return {}
    with db.tx():
        for key in changed:
            masterdata.set_setting(db, key, clean[key], is_secret=BY_KEY[key].kind == "secret")
        audit.record(db, "settings.changed", actor=by, data={"changes": changed})
    return changed


def _check(s, raw):
    if not raw:
        if s.required:
            raise ValueError("required")
        return s.default if s.kind in ("int", "choice", "time") else ""
    if s.kind == "int":
        try:
            n = int(raw)
        except ValueError:
            raise ValueError("must be a whole number") from None
        if (s.low is not None and n < s.low) or (s.high is not None and n > s.high):
            raise ValueError(f"must be between {s.low} and {s.high}")
        return str(n)
    if s.kind == "time":
        try:
            hh, mm = (int(x) for x in raw.split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except (ValueError, AssertionError):
            raise ValueError("must be a time such as 06:00") from None
        return f"{hh:02d}:{mm:02d}"
    if s.kind == "choice":
        if raw not in s.choices:
            raise ValueError(f"must be one of {', '.join(s.choices)}")
        return raw
    if s.kind == "email":
        if "@" not in raw or " " in raw or "." not in raw.rsplit("@", 1)[-1]:
            raise ValueError("not an email address")
        return raw.lower()
    if s.kind == "url":
        u = urlparse(raw)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("must start with https:// (or http://) and contain a host name")
        return raw.rstrip("/")
    if s.kind == "domains":
        domains = [d.strip().lower().lstrip("@") for d in raw.replace(";", ",").split(",") if d.strip()]
        if not domains or any("." not in d or " " in d for d in domains):
            raise ValueError("comma-separated domains such as company.de")
        return ", ".join(domains)
    if s.kind == "dates":
        days = []
        for line in raw.replace(",", "\n").splitlines():
            if line.strip():
                try:
                    days.append(date.fromisoformat(line.strip()).isoformat())
                except ValueError:
                    raise ValueError(f"'{line.strip()}' is not a date (YYYY-MM-DD)") from None
        return "\n".join(sorted(set(days)))
    return raw


# --- SMTP -------------------------------------------------------------

def smtp_config(db, cipher):
    secret = masterdata.get_setting(db, "smtp_password", "")
    return {
        "host": masterdata.get_setting(db, "smtp_host", ""),
        "port": int(masterdata.get_setting(db, "smtp_port", "25")),
        "security": masterdata.get_setting(db, "smtp_security", "none"),
        "verify": masterdata.get_setting(db, "smtp_tls_verify", "yes") == "yes",
        "user": masterdata.get_setting(db, "smtp_user", ""),
        "password": cipher.decrypt_text(secret) if secret else "",
        "sender": masterdata.get_setting(db, "smtp_sender", ""),
    }


def open_smtp(cfg, timeout=15):
    """Connect (and log in only when a user is set). Caller must quit()."""
    context = ssl.create_default_context()
    if not cfg["verify"]:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if cfg["security"] == "tls":
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout)
        if cfg["security"] == "starttls":
            server.starttls(context=context)
    if cfg["user"]:
        server.login(cfg["user"], cfg["password"])
    return server


def send_test_email(db, cipher, to_addr, *, by):
    """Send one test email now (not via the outbox). Returns (ok, message)."""
    cfg = smtp_config(db, cipher)
    if not cfg["host"] or not cfg["sender"]:
        return False, "SMTP server and sender address must be set first."
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to_addr
    msg["Subject"] = "WFTOOL – Testmail / test email"
    msg.set_content("Diese Testmail bestätigt, dass WFTOOL E-Mails versenden kann.\n\n"
                    "This test email confirms that WFTOOL can send email.\n")
    try:
        server = open_smtp(cfg)
        try:
            server.send_message(msg)
        finally:
            server.quit()
        ok, message = True, f"Test email sent to {to_addr}."
    except (OSError, smtplib.SMTPException) as exc:
        ok, message = False, f"Sending failed: {exc.__class__.__name__}: {exc}"
    audit.record(db, "smtp.test", actor=by, data={"to": to_addr, "ok": ok, "message": message,
                                                   "host": cfg["host"], "port": cfg["port"]})
    return ok, message


# --- working days (Saxony) --------------------------------------------

def easter_sunday(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def saxony_holidays(year):
    """Public holidays in Saxony (Sachsen) for `year`, as {date: name}."""
    e = easter_sunday(year)
    nov23 = date(year, 11, 23)
    buss_und_bettag = nov23 - timedelta(days=((nov23.weekday() - 2) % 7) or 7)
    return {
        date(year, 1, 1): "Neujahr",
        e - timedelta(days=2): "Karfreitag",
        e + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        e + timedelta(days=39): "Christi Himmelfahrt",
        e + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 10, 31): "Reformationstag",
        buss_und_bettag: "Buß- und Bettag",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }


def days_off(db, year):
    extra = {date.fromisoformat(d) for d in (masterdata.get_setting(db, "extra_holidays", "") or "").split()
             if d.startswith(str(year))}
    return set(saxony_holidays(year)) | extra


def is_working_day(db, day):
    return day.weekday() < 5 and day not in days_off(db, day.year)


def add_working_days(db, start, count):
    """The date `count` working days after `start`."""
    day = start
    while count > 0:
        day += timedelta(days=1)
        if is_working_day(db, day):
            count -= 1
    return day


LOCAL_TZ = ZoneInfo("Europe/Berlin")


def today():
    """Today's date in local time (Europe/Berlin), which decides working days."""
    return clock.now().astimezone(LOCAL_TZ).date()
