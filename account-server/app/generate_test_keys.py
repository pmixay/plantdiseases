"""Create persistent random keys for the synthetic local beta volume only."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.security.tokens import SigningKeyRing


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _write_new_private_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _require_private_file(path: Path) -> None:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise RuntimeError(f"Unsafe generated key file: {path.name}")


def ensure_test_keys(directory: Path, *, environment: str) -> tuple[Path, Path]:
    if environment.strip().lower() != "test":
        raise RuntimeError("Local beta key generation is allowed only in ENVIRONMENT=test")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    signing_path = directory / "signing.json"
    encryption_path = directory / "encryption.key"
    internal_token_path = directory / "internal.token"

    if not signing_path.exists():
        private_key = Ed25519PrivateKey.generate()
        raw_private = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        document = {
            "active_kid": "local-beta-v1",
            "keys": [{"kid": "local-beta-v1", "private_key": _b64url(raw_private)}],
        }
        _write_new_private_file(signing_path, json.dumps(document, separators=(",", ":")))
    if not encryption_path.exists():
        _write_new_private_file(
            encryption_path,
            _b64url(AESGCM.generate_key(bit_length=256)),
        )
    if not internal_token_path.exists():
        _write_new_private_file(internal_token_path, secrets.token_urlsafe(32))

    _require_private_file(signing_path)
    _require_private_file(encryption_path)
    _require_private_file(internal_token_path)
    SigningKeyRing.from_file(signing_path)
    encoded_encryption_key = encryption_path.read_text(encoding="ascii").strip()
    padding = "=" * (-len(encoded_encryption_key) % 4)
    if (
        len(
            base64.b64decode(
                encoded_encryption_key + padding,
                altchars=b"-_",
                validate=True,
            )
        )
        != 32
    ):
        raise RuntimeError("Generated encryption key is invalid")
    return signing_path, encryption_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    ensure_test_keys(args.directory, environment=os.environ.get("ENVIRONMENT", ""))


if __name__ == "__main__":
    main()
