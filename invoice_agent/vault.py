"""Encrypted credential vault.

Credentials are stored on the firm's own machine, encrypted with a key derived from a
passphrase (scrypt + Fernet). The agent never sends a secret to the model: the model asks
for a field by name and the browser layer types the value in locally.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FIELDS = ("username", "password", "totp_secret")
MAGIC = b"IAV1"


def _key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, path: str | Path, passphrase: str):
        if not passphrase:
            raise VaultError("Set a vault passphrase (INVOICE_AGENT_VAULT_PASSPHRASE).")
        self.path = Path(path)
        self._passphrase = passphrase
        self._data: dict[str, dict[str, str]] = {}
        self._salt = os.urandom(16)
        if self.path.exists():
            blob = self.path.read_bytes()
            if not blob.startswith(MAGIC):
                raise VaultError(f"{self.path} is not a vault file.")
            self._salt, token = blob[4:20], blob[20:]
            try:
                self._data = json.loads(Fernet(_key(passphrase, self._salt)).decrypt(token))
            except InvalidToken as e:
                raise VaultError("Wrong passphrase, or the vault file is damaged.") from e

    def save(self) -> None:
        token = Fernet(_key(self._passphrase, self._salt)).encrypt(json.dumps(self._data).encode())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(MAGIC + self._salt + token)
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def set(self, portal_id: str, field: str, value: str) -> None:
        if field not in FIELDS:
            raise VaultError(f"Unknown field {field!r}; use one of {', '.join(FIELDS)}.")
        self._data.setdefault(portal_id, {})[field] = value

    def has(self, portal_id: str, field: str) -> bool:
        return bool(self._data.get(portal_id, {}).get(field))

    def fields(self, portal_id: str) -> list[str]:
        return sorted(self._data.get(portal_id, {}))

    def portals(self) -> list[str]:
        return sorted(self._data)

    def secret(self, portal_id: str, field: str) -> str:
        """Return the value to type into the page. 'totp' yields a fresh one-time code."""
        entry = self._data.get(portal_id, {})
        if field == "totp":
            if not entry.get("totp_secret"):
                raise VaultError(f"No authenticator secret stored for {portal_id}.")
            return totp(entry["totp_secret"])
        if not entry.get(field):
            raise VaultError(f"No {field} stored for {portal_id}.")
        return entry[field]

    def all_secret_values(self) -> list[str]:
        return [v for entry in self._data.values() for v in entry.values() if v]


def totp(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (SHA-1, the authenticator-app default)."""
    key = base64.b32decode(secret_b32.replace(" ", "").upper() + "=" * (-len(secret_b32.replace(" ", "")) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)
