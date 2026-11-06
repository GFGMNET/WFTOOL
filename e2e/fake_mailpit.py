"""A small stand-in for Mailpit, to run the suite without Docker (scripts/test-local.sh).

SMTP on one port, and the parts of Mailpit's HTTP API the suite uses:
GET /api/v1/messages, GET /api/v1/message/<id>, GET /api/v1/message/<id>/part/<part>, DELETE /api/v1/messages.
"""

import email
import email.policy
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from fake_smtp import FakeSMTP  # noqa: E402


class FakeMailpit:
    def __init__(self, smtp_port=0, http_port=0):
        self.smtp = FakeSMTP()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                outer.smtp.messages.clear()
                self._json({})

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                msgs = list(outer.smtp.messages)
                if parts[:3] == ["api", "v1", "messages"]:
                    items = []
                    for i, m in reversed(list(enumerate(msgs))):
                        msg = email.message_from_string(m["data"], policy=email.policy.default)
                        items.append({"ID": str(i), "Subject": str(msg["Subject"]),
                                      "To": [{"Address": a} for a in m["to"]],
                                      "Attachments": sum(1 for p in msg.iter_attachments())})
                    return self._json({"total": len(items), "messages": items})
                if parts[:3] == ["api", "v1", "message"] and len(parts) >= 4:
                    m = msgs[int(parts[3])]
                    msg = email.message_from_string(m["data"], policy=email.policy.default)
                    atts = list(msg.iter_attachments())
                    if len(parts) == 6 and parts[4] == "part":
                        data = atts[int(parts[5])].get_content()
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return None
                    text = msg.get_body(("plain",))
                    html = msg.get_body(("html",))
                    return self._json({
                        "ID": parts[3], "Subject": str(msg["Subject"]),
                        "To": [{"Address": a} for a in m["to"]],
                        "Text": text.get_content() if text else "", "HTML": html.get_content() if html else "",
                        "Attachments": [{"PartID": str(n), "FileName": a.get_filename(),
                                         "ContentType": a.get_content_type()} for n, a in enumerate(atts)]})
                return self._json({"error": "not found"}, 404)

        self.http = ThreadingHTTPServer(("127.0.0.1", http_port), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.smtp_port = self.smtp.port
        self.http_port = self.http.server_address[1]

    def close(self):
        self.http.shutdown()
        self.smtp.close()
