"""Outbound account mail: SMTP over TLS, a recording mock, and a send queue.

Email sign-in is offered only when a mailer exists. Missing or unsafe mail
settings therefore never stop the service: :func:`build_mailer` returns
``None`` and the email routes answer ``email_auth_unavailable``.

Nothing here logs an address, a code, a credential or a server reply.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("plantdiseases.account")

_IMPLICIT_TLS_PORT = 465
_TIMEOUT_SECONDS = 10
_MAX_PASSWORD_BYTES = 1024


@dataclass(frozen=True, slots=True)
class MailMessage:
    to: str = field(repr=False)
    subject: str
    body: str = field(repr=False)


class Mailer(Protocol):
    def send(self, message: MailMessage) -> None: ...


class SmtpMailer:
    """Authenticated SMTP that never sends over a clear-text connection.

    Port 465 uses TLS from the first byte. Every other port must offer
    STARTTLS; ``smtplib`` raises before any credential is sent when it does
    not. The default context verifies the certificate and the host name.
    """

    def __init__(self, *, host: str, port: int, user: str, password: str, sender: str):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._sender = sender

    def send(self, message: MailMessage) -> None:
        mail = EmailMessage()
        mail["From"] = self._sender
        mail["To"] = message.to
        mail["Subject"] = message.subject
        mail["Date"] = formatdate()
        mail["Message-ID"] = make_msgid(domain=parseaddr(self._sender)[1].rsplit("@", 1)[1])
        mail["Auto-Submitted"] = "auto-generated"
        mail.set_content(message.body)

        context = ssl.create_default_context()
        if self._port == _IMPLICIT_TLS_PORT:
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=_TIMEOUT_SECONDS, context=context
            ) as client:
                client.login(self._user, self._password)
                client.send_message(mail)
            return
        with smtplib.SMTP(self._host, self._port, timeout=_TIMEOUT_SECONDS) as client:
            client.starttls(context=context)
            client.login(self._user, self._password)
            client.send_message(mail)


class MockMailer:
    """Records messages in memory. For tests; no setting can select it."""

    def __init__(self):
        self.outbox: list[MailMessage] = []

    def send(self, message: MailMessage) -> None:
        self.outbox.append(message)


class MailQueue:
    """Sends in the background so response time does not depend on delivery.

    Some requests send mail and others, which must look identical, do not. A
    synchronous send would show which addresses have accounts. The queue is
    bounded: when it is full the message is dropped and the member asks again.
    """

    def __init__(self, mailer: Mailer, *, workers: int = 2, max_pending: int = 100):
        self._mailer = mailer
        self._slots = threading.BoundedSemaphore(max_pending)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="account-mail")

    def send(self, message: MailMessage) -> None:
        if not self._slots.acquire(blocking=False):
            logger.warning("Account mail queue is full; a message was dropped")
            return
        try:
            self._executor.submit(self._deliver, message)
        except RuntimeError:  # shutting down
            self._slots.release()

    def _deliver(self, message: MailMessage) -> None:
        try:
            self._mailer.send(message)
        except Exception as exc:  # noqa: BLE001 - replies can echo the address
            logger.warning("Account mail delivery failed: %s", type(exc).__name__)
        finally:
            self._slots.release()

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


def _read_password(path: Path) -> str | None:
    try:
        if not path.is_absolute() or not path.is_file():
            return None
        details = path.stat()
        if stat.S_IMODE(details.st_mode) & 0o077 or details.st_size > _MAX_PASSWORD_BYTES:
            return None
        password = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return password or None


def build_mailer(settings: Settings) -> Mailer | None:
    """The configured SMTP mailer, or ``None`` when mail is not usable.

    Warnings name the setting at fault and never repeat its value.
    """

    values = {
        "SMTP_HOST": settings.smtp_host,
        "SMTP_PORT": settings.smtp_port,
        "SMTP_USER": settings.smtp_user,
        "SMTP_FROM": settings.smtp_from,
        "SMTP_PASSWORD_FILE": settings.smtp_password_file,
    }
    missing = [name for name, value in values.items() if value is None]
    if missing:
        logger.warning("Email sign-in is off: unset %s", ", ".join(missing))
        return None
    port_text = str(settings.smtp_port)
    port = int(port_text) if port_text.isascii() and port_text.isdecimal() else 0
    if not 1 <= port <= 65_535:
        logger.warning("Email sign-in is off: SMTP_PORT is invalid")
        return None
    sender = str(settings.smtp_from)
    address = parseaddr(sender)[1]
    if "\r" in sender or "\n" in sender or address.count("@") != 1 or not address.isascii():
        logger.warning("Email sign-in is off: SMTP_FROM is invalid")
        return None
    password = _read_password(Path(str(settings.smtp_password_file)))
    if password is None:
        logger.warning(
            "Email sign-in is off: SMTP_PASSWORD_FILE must be an absolute, "
            "non-empty, owner-only file"
        )
        return None
    return SmtpMailer(
        host=str(settings.smtp_host),
        port=port,
        user=str(settings.smtp_user),
        password=password,
        sender=sender,
    )
