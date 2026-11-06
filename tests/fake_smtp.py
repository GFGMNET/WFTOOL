"""A tiny SMTP server for tests: accepts mail without login, like an IP-allowed relay."""

import socketserver
import threading


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        self._send("220 fake relay ready")
        data_mode, lines, mail_from, rcpts = False, [], None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if data_mode:
                if line == ".":
                    server.messages.append({"from": mail_from, "to": rcpts, "data": "\n".join(lines)})
                    data_mode, lines, rcpts = False, [], []
                    self._send("250 queued")
                else:
                    lines.append(line[1:] if line.startswith("..") else line)
                continue
            cmd = line[:4].upper()
            if cmd in ("EHLO", "HELO"):
                self._send("250 fake")
            elif cmd == "AUTH":
                server.auth_attempts += 1
                self._send("502 no auth here")
            elif cmd == "MAIL":
                mail_from = line[10:].strip("<> ")
                self._send("250 ok")
            elif cmd == "RCPT":
                rcpts.append(line[8:].strip("<> "))
                self._send("250 ok")
            elif cmd == "DATA":
                data_mode = True
                self._send("354 go")
            elif cmd == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("250 ok")

    def _send(self, text):
        self.wfile.write((text + "\r\n").encode())


class FakeSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages = []
        self.auth_attempts = 0
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def close(self):
        self.shutdown()
        self.server_close()
