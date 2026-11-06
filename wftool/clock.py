"""Single source of the current time, so tests can control it."""

from datetime import datetime, timezone

_override = None


def now():
    """Current time as an aware UTC datetime."""
    return _override if _override is not None else datetime.now(timezone.utc)


def now_iso():
    """Current time as ISO 8601 text in UTC, second precision."""
    return now().replace(microsecond=0).isoformat()


def set_now(value):
    """Freeze the clock (tests). Pass None to return to real time."""
    global _override
    _override = value
