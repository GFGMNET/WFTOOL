"""Fingerprints and encryption of secret values.

The encryption key lives in a file outside the data folder (design: "Key file
... backed up separately"). Secret form values are stored as {"$enc": token}.
"""

import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def canonical_json(value):
    """Stable JSON text: sorted keys, no extra spaces, UTF-8 characters kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value):
    """SHA-256 hex digest of the canonical JSON form of `value` (or of bytes)."""
    data = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class Cipher:
    def __init__(self, key):
        self._fernet = Fernet(key)

    @classmethod
    def from_key_file(cls, path, create=True):
        """Load the key from `path`; create it (mode 600) if missing and `create` is set."""
        path = Path(path)
        if not path.exists():
            if not create:
                raise FileNotFoundError(f"Key file not found: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        return cls(path.read_bytes().strip())

    @classmethod
    def generate(cls):
        return cls(Fernet.generate_key())

    def encrypt_text(self, text):
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt_text(self, token):
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Secret value cannot be decrypted with this key") from exc

    def encrypt_bytes(self, data):
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token):
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise ValueError("File cannot be decrypted with this key") from exc
