"""Encrypted files in the data folder: signatures, paper scans, PDFs for emails.

Each file is encrypted with the same key as the secret form values, so a copy
of the data folder alone reveals nothing (design: "Stored files"). Paths stored
in the database are relative to the files folder.
"""

import os
import secrets
from pathlib import Path


class FileStore:
    def __init__(self, root, cipher):
        self.root = Path(root)
        self.cipher = cipher
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, kind, data, suffix=""):
        """Encrypt and store `data`; returns the relative path to keep in the database."""
        folder = self.root / kind
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{secrets.token_hex(16)}{suffix}.enc"
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self.cipher.encrypt_bytes(data))
        return f"{kind}/{name}"

    def load(self, rel):
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("path outside the files folder")
        return self.cipher.decrypt_bytes(path.read_bytes())

    def delete(self, rel):
        path = (self.root / rel).resolve()
        if self.root.resolve() in path.parents and path.exists():
            path.unlink()
