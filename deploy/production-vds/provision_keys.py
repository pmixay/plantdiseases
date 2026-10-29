"""Operator provisioning of persistent account-beta secrets (non-test deployment).

Runs once inside a network-disabled container built from the deployed account
image, as the service UID, against the two Compose-managed secret volumes. It
writes the same file formats the service loads (the repo's test-only generator
refuses any ENVIRONMENT other than "test", so it is intentionally not used).

Never overwrites an existing file. Never prints key material.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.security.tokens import SigningKeyRing

KEY_DIR = Path("/run/account-secrets")
TOKEN_DIR = Path("/run/internal-token")
ACTIVE_KID = "plantd-prod-2026-10"


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def write_new(path: Path, content: str, mode: int) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    except FileExistsError:
        return False
    os.fchmod(descriptor, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    return True


def require_mode(path: Path, forbidden: int) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & forbidden:
        raise SystemExit(f"unsafe permissions on {path.name}")


def main() -> int:
    for directory, mode in ((KEY_DIR, 0o700), (TOKEN_DIR, 0o750)):
        directory.mkdir(mode=mode, parents=True, exist_ok=True)
        directory.chmod(mode)

    signing = KEY_DIR / "signing.json"
    encryption = KEY_DIR / "encryption.key"
    token = TOKEN_DIR / "internal.token"

    created = []
    seed = Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    document = {"active_kid": ACTIVE_KID, "keys": [{"kid": ACTIVE_KID, "private_key": b64url(seed)}]}
    if write_new(signing, json.dumps(document, separators=(",", ":")), 0o600):
        created.append("signing.json")
    if write_new(encryption, b64url(AESGCM.generate_key(bit_length=256)), 0o600):
        created.append("encryption.key")
    if write_new(token, secrets.token_urlsafe(48), 0o640):
        created.append("internal.token")

    require_mode(signing, 0o077)
    require_mode(encryption, 0o077)
    require_mode(token, 0o027)
    SigningKeyRing.from_file(signing)
    raw = base64.b64decode(
        encryption.read_text(encoding="ascii").strip() + "==", altchars=b"-_", validate=False
    )
    if len(raw) != 32:
        raise SystemExit("encryption key has the wrong length")
    if not 32 <= len(token.read_text(encoding="ascii").strip()) <= 512:
        raise SystemExit("internal token has the wrong length")

    print("created:", ", ".join(created) if created else "(nothing; all files already existed)")
    print("validated: signing key ring, encryption key length, internal token length, modes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
