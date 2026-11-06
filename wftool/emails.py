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


# --- emails the app sends on its own (to the starter and the admin) ---------
# Placeholders: {request_no} {workflow} {step} {person} {decision} {reason} {url} {days}

SYSTEM = {
    "task_decided": {
        "subject": {"de": "{workflow} {request_no}: {decision} ({step})",
                    "en": "{workflow} {request_no}: {decision} ({step})"},
        "body": {"de": "Guten Tag,\n\nim Antrag {request_no} ({workflow}) wurde der Schritt „{step}“ erledigt: "
                       "{decision} von {person}.\n\nZum Antrag:\n{url}",
                 "en": "Hello,\n\nin request {request_no} ({workflow}) the step \"{step}\" is done: "
                       "{decision} by {person}.\n\nOpen the request:\n{url}"},
    },
    "request_returned": {
        "subject": {"de": "Abgelehnt: {workflow} {request_no}", "en": "Rejected: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nIhr Antrag {request_no} ({workflow}) wurde im Schritt „{step}“ von {person} "
                       "abgelehnt.\n\nBegründung: {reason}\n\nSie können den Antrag ändern und erneut senden; "
                       "alle Schritte laufen dann noch einmal:\n{url}",
                 "en": "Hello,\n\nyour request {request_no} ({workflow}) was rejected by {person} in the step "
                       "\"{step}\".\n\nReason: {reason}\n\nYou can change the request and send it again; all steps "
                       "then run once more:\n{url}"},
    },
    "request_completed": {
        "subject": {"de": "Abgeschlossen: {workflow} {request_no}", "en": "Completed: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nIhr Antrag {request_no} ({workflow}) ist abgeschlossen; alle Schritte sind "
                       "erledigt.\n\nZum Antrag:\n{url}",
                 "en": "Hello,\n\nyour request {request_no} ({workflow}) is completed; all steps are done.\n\n"
                       "Open the request:\n{url}"},
    },
    "request_cancelled": {
        "subject": {"de": "Storniert: {workflow} {request_no}", "en": "Cancelled: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nder Antrag {request_no} ({workflow}) wurde storniert.\n\nBegründung: {reason}"
                       "\n\nZum Antrag:\n{url}",
                 "en": "Hello,\n\nrequest {request_no} ({workflow}) was cancelled.\n\nReason: {reason}\n\n"
                       "Open the request:\n{url}"},
    },
    "paper_requested": {
        "subject": {"de": "Unterschrift auf Papier: {workflow} {request_no}",
                    "en": "Signature on paper: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\n{person} unterschreibt den Antrag {request_no} ({workflow}) auf Papier.\n\n"
                       "Bitte drucken Sie die Seite auf der Antragsseite aus, lassen Sie sie unterschreiben und laden "
                       "Sie den Scan dort hoch:\n{url}",
                 "en": "Hello,\n\n{person} will sign request {request_no} ({workflow}) on paper.\n\n"
                       "Please print the page from the request page, have it signed, and upload the scan there:\n{url}"},
    },
    "task_expired": {
        "subject": {"de": "Keine Antwort: {workflow} {request_no}", "en": "No response: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nder Link für den Schritt „{step}“ im Antrag {request_no} ({workflow}) ist "
                       "abgelaufen, ohne dass {person} geantwortet hat. Der Antrag wartet.\n\nAuf der Antragsseite "
                       "können Sie den Link erneut senden; der Admin kann die Aufgabe einer anderen Person "
                       "zuweisen:\n{url}",
                 "en": "Hello,\n\nthe link for the step \"{step}\" in request {request_no} ({workflow}) expired "
                       "without a response from {person}. The request is waiting.\n\nOn the request page you can "
                       "send the link again; the admin can give the task to someone else:\n{url}"},
    },
    "starter_notice": {
        "subject": {"de": "Noch offen: {workflow} {request_no}", "en": "Still open: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nder Schritt „{step}“ im Antrag {request_no} ({workflow}) ist seit {days} "
                       "Arbeitstagen offen ({person}).\n\nAuf der Antragsseite können Sie den Link erneut senden:\n{url}",
                 "en": "Hello,\n\nthe step \"{step}\" in request {request_no} ({workflow}) has been open for {days} "
                       "working days ({person}).\n\nOn the request page you can send the link again:\n{url}"},
    },
    "paper_reminder": {
        "subject": {"de": "Erinnerung – Scan fehlt: {workflow} {request_no}",
                    "en": "Reminder – scan missing: {workflow} {request_no}"},
        "body": {"de": "Guten Tag,\n\nfür den Antrag {request_no} ({workflow}) fehlt noch der Scan der "
                       "Unterschrift von {person}. Bitte laden Sie ihn auf der Antragsseite hoch:\n{url}",
                 "en": "Hello,\n\nthe scan of the signature of {person} for request {request_no} ({workflow}) is "
                       "still missing. Please upload it on the request page:\n{url}"},
    },
}

REMINDER_PREFIX = {"de": "Erinnerung: ", "en": "Reminder: "}
BUTTON = {"task": {"de": "Aufgabe öffnen", "en": "Open the task"},
          "request": {"de": "Antrag öffnen", "en": "Open the request"}}
FOOTER = {"de": "Diese E-Mail wurde von WFTOOL automatisch versendet.",
          "en": "This email was sent automatically by WFTOOL."}
DETAILS = {"de": "Angaben im Antrag", "en": "Details of the request"}


def system_texts(kind, lang, context):
    texts = SYSTEM[kind]
    return render(texts["subject"][lang], context), render(texts["body"][lang], context)


_URL = re.compile(r"(https?://[^\s<]+)")


def html_email(lang, body_text, *, button=None, details=None, title=None):
    """A small HTML email that works in Outlook: one column, inline styles, a button for the link.

    `body_text` is the rendered plain text; it is escaped, links become clickable.
    `button` is (url, label); `details` a list of (label, value) shown as a table.
    """
    import html as _html

    def para(block):
        safe = _html.escape(block)
        safe = _URL.sub(lambda m: f'<a href="{m.group(1)}" style="color:#2B4C9B;word-break:break-all">'
                                  f'{m.group(1)}</a>', safe)
        return f'<p style="margin:0 0 14px">{safe.replace(chr(10), "<br>")}</p>'

    paragraphs = "".join(para(b) for b in body_text.split("\n\n") if b.strip())
    parts = [
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
        f"<title>{_html.escape(title or '')}</title></head>",
        '<body style="margin:0;padding:0;background:#F2F4F7">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7">'
        '<tr><td align="center" style="padding:24px 12px">',
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;'
        'background:#FFFFFF;border:1px solid #D3D9E2;border-top:4px solid #2B4C9B">',
        '<tr><td style="padding:24px 28px;font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.5;'
        'color:#1D2633">',
        paragraphs,
    ]
    if button:
        url, label = button
        parts.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px"><tr>'
            f'<td style="background:#2B4C9B;border-radius:4px"><a href="{_html.escape(url)}" '
            'style="display:inline-block;padding:11px 22px;font-family:Segoe UI,Arial,sans-serif;font-size:15px;'
            f'font-weight:bold;color:#FFFFFF;text-decoration:none">{_html.escape(label)}</a></td></tr></table>')
    if details:
        rows = "".join(
            f'<tr><td style="padding:5px 12px 5px 0;color:#55606F;vertical-align:top;border-bottom:1px solid #E6EAF0">'
            f'{_html.escape(k)}</td><td style="padding:5px 0;vertical-align:top;border-bottom:1px solid #E6EAF0">'
            f'{_html.escape(v)}</td></tr>' for k, v in details)
        parts.append(f'<p style="margin:18px 0 6px;font-weight:bold">{_html.escape(DETAILS[lang])}</p>'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px;width:100%">'
                     f'{rows}</table>')
    parts.append('</td></tr><tr><td style="padding:12px 28px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;'
                 f'color:#55606F;border-top:1px solid #D3D9E2">{_html.escape(FOOTER[lang])}</td></tr>')
    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)
