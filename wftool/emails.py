"""Email subject and text of each step, with placeholders.

Each step may carry its own texts:

    "email": {"subject": {"de": "...", "en": "..."}, "body": {"de": "...", "en": "..."}}

An empty or missing text falls back to the default of the step type below.
Placeholders are written in braces: the built-in ones listed in PLACEHOLDERS,
plus the id of any form field outside a repeatable section, for example
`{employee}`. Secret fields never appear in an email. Phase 4 sends the
emails; `render()` is what it uses.
"""

import re

PLACEHOLDERS = {
    "request_no": {"de": "Antragsnummer, z. B. 2026-0042", "en": "Request number, e.g. 2026-0042"},
    "workflow": {"de": "Name des Workflows", "en": "Name of the workflow"},
    "step": {"de": "Name des Schritts", "en": "Name of the step"},
    "starter": {"de": "Wer den Antrag gestartet hat", "en": "Who started the request"},
    "link": {"de": "Persönlicher Link zur Aufgabe", "en": "Personal link to the task"},
}

_TOKEN = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")

_DE_LINK = ("\n\nBitte öffnen Sie diesen Link:\n{link}\n\n"
            "Der Link ist persönlich. Bitte leiten Sie diese E-Mail nicht weiter.")
_EN_LINK = ("\n\nPlease open this link:\n{link}\n\n"
            "The link is personal. Please do not forward this email.")

DEFAULTS = {
    "fill": {
        "subject": {"de": "Bitte ausfüllen: {workflow} {request_no}",
                    "en": "Please fill in: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nfür den Antrag {request_no} ({workflow}) von {starter} "
                       "werden noch Angaben von Ihnen benötigt (Schritt „{step}“)." + _DE_LINK,
                 "en": "Hello,\n\nrequest {request_no} ({workflow}) from {starter} needs some details "
                       "from you (step \"{step}\")." + _EN_LINK},
    },
    "approve": {
        "subject": {"de": "Bitte genehmigen: {workflow} {request_no}",
                    "en": "Please approve: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\n{starter} bittet um Ihre Genehmigung für den Antrag {request_no} "
                       "({workflow}). Sie können genehmigen oder mit Begründung ablehnen." + _DE_LINK,
                 "en": "Hello,\n\n{starter} asks for your approval of request {request_no} ({workflow}). "
                       "You can approve it or reject it with a reason." + _EN_LINK},
    },
    "fill_approve": {
        "subject": {"de": "Bitte ergänzen und genehmigen: {workflow} {request_no}",
                    "en": "Please complete and approve: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nbitte ergänzen Sie die fehlenden Angaben im Antrag {request_no} "
                       "({workflow}) von {starter} und genehmigen Sie ihn, oder lehnen Sie ihn mit "
                       "Begründung ab." + _DE_LINK,
                 "en": "Hello,\n\nplease complete the missing details of request {request_no} ({workflow}) "
                       "from {starter} and approve it, or reject it with a reason." + _EN_LINK},
    },
    "approve_per_item": {
        "subject": {"de": "Freigabe benötigt: {workflow} {request_no}",
                    "en": "Approval needed: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nim Antrag {request_no} ({workflow}) wurde ein Eintrag gewählt, "
                       "für den Sie die Freigabe erteilen." + _DE_LINK,
                 "en": "Hello,\n\nrequest {request_no} ({workflow}) contains an item that you approve."
                       + _EN_LINK},
    },
    "sign": {
        "subject": {"de": "Bitte unterschreiben: {workflow} {request_no}",
                    "en": "Please sign: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nbitte prüfen Sie das Dokument {request_no} ({workflow}) und "
                       "unterschreiben Sie es auf der Seite. Wenn etwas nicht stimmt, können Sie es "
                       "mit Begründung zurückgeben." + _DE_LINK,
                 "en": "Hello,\n\nplease review document {request_no} ({workflow}) and sign it on the page. "
                       "If something is wrong, you can send it back with a reason." + _EN_LINK},
    },
    "confirm": {
        "subject": {"de": "Bitte bestätigen: {step} – {request_no}",
                    "en": "Please confirm: {step} – {request_no}"},
        "body": {"de": "Guten Tag,\n\nder Antrag {request_no} ({workflow}) wartet auf Ihre Bestätigung "
                       "„{step}“. Wer aus Ihrer Gruppe zuerst bestätigt, erledigt es für alle." + _DE_LINK,
                 "en": "Hello,\n\nrequest {request_no} ({workflow}) is waiting for your confirmation "
                       "\"{step}\". Whoever in your group confirms first does it for everyone." + _EN_LINK},
    },
    "notify": {
        "subject": {"de": "Zur Information: {workflow} {request_no}",
                    "en": "For your information: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nzur Information: Antrag {request_no} ({workflow}) von {starter}, "
                       "Schritt „{step}“.\n\nDiese E-Mail wurde automatisch versendet.",
                 "en": "Hello,\n\nfor your information: request {request_no} ({workflow}) from {starter}, "
                       "step \"{step}\".\n\nThis email was sent automatically."},
    },
}


def placeholders_in(text):
    """Names of the placeholders used in `text`, in order of first use."""
    return list(dict.fromkeys(_TOKEN.findall(text or "")))


def texts_for(step, lang="de"):
    """(subject, body) of a step in `lang`, falling back to the step type's defaults."""
    own = step.get("email") or {}
    default = DEFAULTS.get(step.get("type"), DEFAULTS["notify"])
    out = []
    for part in ("subject", "body"):
        value = own.get(part)
        text = value.get(lang) if isinstance(value, dict) else (value if lang == "de" else None)
        out.append(text if text and text.strip() else default[part][lang])
    return tuple(out)


def render(text, context):
    """Replace {name} with context[name]; unknown names stay as they are."""
    return _TOKEN.sub(lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0), text)
