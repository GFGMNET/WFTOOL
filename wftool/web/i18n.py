"""German translations of the interface. The English text is the key; a missing
entry falls back to English, so a new string never breaks a page."""

DE = {
    # navigation and common
    "Home": "Start",
    "Accounts": "Konten",
    "People and groups": "Personen und Gruppen",
    "Lists": "Listen",
    "Settings": "Einstellungen",
    "Imports": "Importe",
    "Who may start": "Wer darf starten",
    "Sign out": "Abmelden",
    "Change password": "Passwort ändern",
    "Save": "Speichern",
    "Save changes": "Änderungen speichern",
    "Cancel": "Abbrechen",
    "Active": "Aktiv",
    "Inactive": "Inaktiv",
    "Activate": "Aktivieren",
    "Deactivate": "Deaktivieren",
    "Email": "E-Mail",
    "Name": "Name",
    "Role": "Rolle",
    "Language": "Sprache",
    "Status": "Status",
    "Search": "Suchen",
    "Administration": "Verwaltung",
    "Signed in as": "Angemeldet als",
    "Changes saved.": "Änderungen gespeichert.",
    "Please correct the marked fields.": "Bitte die markierten Felder korrigieren.",
    "This form expired. Please try again.": "Das Formular ist abgelaufen. Bitte erneut versuchen.",
    "You don't have access to this page.": "Sie haben keinen Zugriff auf diese Seite.",
    # roles
    "admin": "Admin",
    "starter": "Antragsteller",
    "auditor": "Prüfer",
    # login
    "Sign in": "Anmelden",
    "Username": "Benutzername",
    "Password": "Passwort",
    "Username or password is wrong.": "Benutzername oder Passwort ist falsch.",
    "Too many wrong attempts. Try again later.":
        "Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.",
    "This account is deactivated.": "Dieses Konto ist deaktiviert.",
    "You were signed out after 60 minutes without activity.":
        "Sie wurden nach 60 Minuten ohne Aktivität abgemeldet.",
    "Sign in to manage requests and approvals.": "Melden Sie sich an, um Anträge und Freigaben zu verwalten.",
    # password
    "Current password": "Aktuelles Passwort",
    "New password": "Neues Passwort",
    "Repeat new password": "Neues Passwort wiederholen",
    "Choose a new password before you continue.": "Bitte wählen Sie ein neues Passwort, bevor Sie fortfahren.",
    "At least 12 characters.": "Mindestens 12 Zeichen.",
    "The two new passwords are different.": "Die beiden neuen Passwörter sind verschieden.",
    "Password changed.": "Passwort geändert.",
    # home
    "Start a request": "Antrag starten",
    "No workflow is open to you yet. An admin sets who may start each workflow.":
        "Noch kein Workflow ist für Sie freigegeben. Ein Admin legt fest, wer welchen Workflow starten darf.",
    "Starting requests follows in the next build phase.": "Das Starten von Anträgen folgt in der nächsten Ausbaustufe.",
    "Needs attention": "Bitte prüfen",
    "These settings are still empty:": "Diese Einstellungen sind noch leer:",
    "The last import was rejected:": "Der letzte Import wurde abgelehnt:",
    "The default password of admin is still active.": "Das Standardpasswort von admin ist noch aktiv.",
    # accounts
    "New account": "Neues Konto",
    "Create account": "Konto anlegen",
    "Temporary password": "Vorläufiges Passwort",
    "The user must change it at the first sign-in.": "Der Benutzer muss es bei der ersten Anmeldung ändern.",
    "Email of the AD user": "E-Mail des AD-Benutzers",
    "Needed for starters: it decides which workflows they may start.":
        "Für Antragsteller nötig: daran hängt, welche Workflows sie starten dürfen.",
    "Last sign-in": "Letzte Anmeldung",
    "never": "nie",
    "Set temporary password": "Vorläufiges Passwort setzen",
    "Locked until": "Gesperrt bis",
    "must change password": "muss Passwort ändern",
    "Account created.": "Konto angelegt.",
    "Temporary password set.": "Vorläufiges Passwort gesetzt.",
    # people
    "Users": "Benutzer",
    "Groups": "Gruppen",
    "Department": "Abteilung",
    "Manager": "Vorgesetzter",
    "Members": "Mitglieder",
    "Users and groups come from the AD import and are changed in AD, not here.":
        "Benutzer und Gruppen kommen aus dem AD-Import und werden im AD geändert, nicht hier.",
    "No users imported yet.": "Noch keine Benutzer importiert.",
    "No groups imported yet.": "Noch keine Gruppen importiert.",
    "Show inactive": "Inaktive zeigen",
    # lists
    "Entries": "Einträge",
    "Source": "Quelle",
    "CSV import": "CSV-Import",
    "This app": "Diese App",
    "New list": "Neue Liste",
    "List id": "Listen-ID",
    "Used in the form designer; a-z, 0-9 and _.": "Wird im Formular-Designer verwendet; a-z, 0-9 und _.",
    "Create list": "Liste anlegen",
    "Add entry": "Eintrag hinzufügen",
    "Rename": "Umbenennen",
    "Move up": "Nach oben",
    "Move down": "Nach unten",
    "Needs approval": "Freigabe nötig",
    "Approver": "Freigeber",
    "yes": "ja",
    "no": "nein",
    "This list comes from the CSV import. Change software.csv to change it.":
        "Diese Liste kommt aus dem CSV-Import. Zum Ändern software.csv anpassen.",
    "Deactivated entries are hidden in new forms but stay in old requests.":
        "Deaktivierte Einträge erscheinen nicht mehr in neuen Formularen, bleiben aber in alten Anträgen.",
    "No entries yet.": "Noch keine Einträge.",
    "Back to lists": "Zurück zu den Listen",
    # settings
    "Email (SMTP)": "E-Mail (SMTP)",
    "General": "Allgemein",
    "Deadlines": "Fristen",
    "Import": "Import",
    "For a relay that allows this server by IP address, leave user and password empty.":
        "Bei einem Relay, das diesen Server per IP-Adresse zulässt, Benutzer und Passwort leer lassen.",
    "Leave empty to keep the saved password.": "Leer lassen, um das gespeicherte Passwort zu behalten.",
    "Remove saved password": "Gespeichertes Passwort entfernen",
    "Send test email": "Testmail senden",
    "Send to": "Senden an",
    "Public holidays in Saxony are included automatically.":
        "Die Feiertage in Sachsen sind automatisch enthalten.",
    "required": "Pflichtfeld",
    "none": "keine",
    # imports
    "Run import now": "Import jetzt ausführen",
    "Import folder": "Importordner",
    "File": "Datei",
    "Time": "Zeit",
    "Added": "Neu",
    "Changed": "Geändert",
    "Deactivated": "Deaktiviert",
    "Skipped rows": "Übersprungene Zeilen",
    "Message": "Meldung",
    "imported": "importiert",
    "rejected": "abgelehnt",
    "unchanged": "unverändert",
    "No imports yet. Put users.csv, groups.csv or software.csv into the import folder.":
        "Noch keine Importe. users.csv, groups.csv oder software.csv in den Importordner legen.",
    "No CSV files found in the import folder.": "Keine CSV-Dateien im Importordner gefunden.",
    "The folder is checked every 5 minutes; files that did not change are skipped.":
        "Der Ordner wird alle 5 Minuten geprüft; unveränderte Dateien werden übersprungen.",
    # start permissions
    "Workflow": "Workflow",
    "Members of these groups may start the workflow. Admins may start every workflow.":
        "Mitglieder dieser Gruppen dürfen den Workflow starten. Admins dürfen jeden Workflow starten.",
    "No workflow published yet. Workflows are built in the builder (next phase).":
        "Noch kein Workflow veröffentlicht. Workflows entstehen im Builder (nächste Ausbaustufe).",
    "no group": "keine Gruppe",
}


def translate(text, lang):
    if lang == "de":
        return DE.get(text, text)
    return text
