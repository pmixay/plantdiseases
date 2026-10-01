"""Push notifications via Firebase Cloud Messaging (FCM HTTP v1).

Stores each install's FCM token under its anonymous pseudo-id and sends a
high-priority *data* message when someone leaves a second opinion on the
owner's shared scan — so the reply reaches a closed / idle device, which the
client's WorkManager poll cannot guarantee.

Entirely optional: without ``FCM_CREDENTIALS_FILE`` (a Firebase service-account
JSON) every send is a silent no-op and the client falls back to polling. The
``firebase_admin`` dependency is imported lazily, so the server runs (and the
tests pass) whether or not it is installed.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat layout
    from config import get_str

logger = logging.getLogger("plantdiseases.push")

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "push.db"
PUSH_DB_PATH = Path(get_str("PUSH_DB_PATH", str(_DEFAULT_DB_PATH)))

# Path to a Firebase service-account JSON. Unset ⇒ push disabled (no-op).
FCM_CREDENTIALS_FILE = get_str("FCM_CREDENTIALS_FILE", "")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_tokens (
    token          TEXT    PRIMARY KEY,
    user_pseudo_id TEXT    NOT NULL,
    platform       TEXT    NOT NULL DEFAULT 'android',
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_push_user ON push_tokens(user_pseudo_id);
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False

_fcm_app = None
_fcm_ready: bool | None = None  # None = not yet resolved


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(
        str(PUSH_DB_PATH), check_same_thread=False, isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        with _connect() as conn:
            conn.executescript(_SCHEMA)
        _initialised = True


def register(user_pseudo_id: str, token: str, platform: str = "android") -> None:
    """Upsert one device token (keyed by the token, which is globally unique)."""
    uid = (user_pseudo_id or "").strip()
    tok = (token or "").strip()
    if not uid or not tok:
        return
    init_db()
    now = int(time.time())
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO push_tokens (token, user_pseudo_id, platform, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET "
            "  user_pseudo_id = excluded.user_pseudo_id, "
            "  platform = excluded.platform, "
            "  updated_at = excluded.updated_at",
            (tok, uid, (platform or "android").strip()[:16], now),
        )


def tokens_for(user_pseudo_id: str) -> list[str]:
    uid = (user_pseudo_id or "").strip()
    if not uid:
        return []
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT token FROM push_tokens WHERE user_pseudo_id = ?", (uid,),
        ).fetchall()
    return [str(r[0]) for r in rows]


def _delete_tokens(tokens: list[str]) -> None:
    if not tokens:
        return
    init_db()
    with _write_lock, _connect() as conn:
        conn.executemany(
            "DELETE FROM push_tokens WHERE token = ?", [(t,) for t in tokens],
        )


def _ensure_fcm() -> bool:
    """Lazily initialise the Firebase Admin app. Returns False (push disabled)
    when no credentials are configured or the SDK isn't installed."""
    global _fcm_app, _fcm_ready
    if _fcm_ready is not None:
        return _fcm_ready
    if not FCM_CREDENTIALS_FILE:
        _fcm_ready = False
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(FCM_CREDENTIALS_FILE)
        _fcm_app = firebase_admin.initialize_app(cred, name="plantdiseases-push")
        _fcm_ready = True
    except Exception:  # noqa: BLE001 - credential/provider details stay private
        logger.warning("FCM initialization failed; push disabled")
        _fcm_ready = False
    return _fcm_ready


def send_opinion_push(
    owner_pseudo_id: str, scan_id: str, disease: str, note: str | None = None,
    nickname: str | None = None,
) -> int:
    """Send a high-priority data push to every device the owner registered.

    The payload carries the suggested-diagnosis class key (localised on-device),
    the responder's optional free-text note, and their plant-vibe nickname, so
    the notification can name who weighed in. Returns the number of messages
    attempted (0 when push is disabled or the owner has no tokens). Stale tokens
    (unregistered / sender mismatch) are pruned. Best-effort — never raises into
    the request path.
    """
    if not _ensure_fcm():
        return 0
    toks = tokens_for(owner_pseudo_id)
    if not toks:
        return 0
    try:
        from firebase_admin import messaging
    except Exception:  # noqa: BLE001 - pragma: no cover
        return 0

    # FCM data values must be strings; cap the note so the payload stays small.
    data = {
        "type": "opinion",
        "scan_id": str(scan_id),
        "disease": str(disease or ""),
        "note": (note or "").strip()[:240],
        "nickname": (nickname or "").strip()[:48],
    }
    stale: list[str] = []
    sent = 0
    for tok in toks:
        try:
            messaging.send(
                messaging.Message(
                    token=tok,
                    data=data,
                    android=messaging.AndroidConfig(priority="high"),
                ),
                app=_fcm_app,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            # Prune tokens FCM tells us are dead; just log the rest.
            if exc.__class__.__name__ in ("UnregisteredError", "SenderIdMismatchError"):
                stale.append(tok)
            else:
                logger.debug("FCM send failed for a token")
    if stale:
        _delete_tokens(stale)
    return sent
