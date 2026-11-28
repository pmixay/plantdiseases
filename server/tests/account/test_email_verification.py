"""Verified email sign-in: mail settings, codes, reset, linking and erasure."""

from __future__ import annotations

import re
import smtplib
import ssl
import uuid
from datetime import timedelta
from pathlib import Path

import app.mail as mail_module
import pytest
from app.config import Settings
from app.db.models import (
    Account,
    EmailCode,
    PasswordCredential,
    PendingPasswordCredential,
    PendingSignup,
)
from app.mail import MailMessage, MailQueue, MockMailer, SmtpMailer, build_mailer
from app.mail_texts import already_registered_message, code_message
from app.main import create_app
from app.security.push_crypto import PushTokenProtector
from app.services.email_codes import EmailAbuseLimits
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import test_account_api as harness

api = harness.api

PASSWORD = "correct horse garden staple"
NEW_PASSWORD = "a different long garden passphrase"


def _post(api, path: str, body: dict, token: str | None = None):
    headers = api.headers(token) if token else None
    return api.client.post(path, json=body, headers=headers)


def _register(api, email: str, installation: uuid.UUID | None = None):
    installation = installation or uuid.uuid4()
    response = _post(
        api,
        "/v1/auth/email/register",
        {"email": email, "password": PASSWORD, "installation_id": str(installation)},
    )
    return response, installation


def _verify(api, pending_token: str, installation: uuid.UUID, code: str):
    return _post(
        api,
        "/v1/auth/email/verify",
        {
            "pending_signup_token": pending_token,
            "code": code,
            "installation_id": str(installation),
        },
    )


def _complete(api, pending_token: str, installation: uuid.UUID):
    return _post(
        api,
        "/v1/auth/signup/complete",
        {
            "pending_signup_token": pending_token,
            "completion_id": str(uuid.uuid4()),
            "nickname": "CodeLeaf",
            "installation_id": str(installation),
        },
    )


def _login(api, email: str, password: str):
    return _post(
        api,
        "/v1/auth/email/login",
        {"email": email, "password": password, "installation_id": str(uuid.uuid4())},
    )


def _reset_request(api, email: str):
    return _post(api, "/v1/auth/email/password-reset/request", {"email": email})


def _reset_confirm(api, email: str, code: str, password: str = NEW_PASSWORD):
    return _post(
        api,
        "/v1/auth/email/password-reset/confirm",
        {"email": email, "code": code, "new_password": password},
    )


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _outcome(response) -> tuple[int, str | None]:
    body = response.json() if response.content else {}
    return response.status_code, body.get("error", {}).get("code")


# ── mail settings ─────────────────────────────────────────────────────


def _mail_env(tmp_path: Path) -> dict[str, str]:
    secret = tmp_path / "smtp-password"
    secret.write_text("synthetic-smtp-password\n", encoding="utf-8")
    secret.chmod(0o400)
    return {
        "ENVIRONMENT": "test",
        "AUTH_PROVIDER_MODE": "mock",
        "ACCOUNT_ISSUER": "http://beta-account-api:8000",
        "ACCOUNT_AUDIENCE": "plantdiseases-app",
        "ACCOUNT_DATABASE_URL": (
            "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
        ),
        "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
        "EMAIL_PASSWORD_AUTH_ENABLED": "true",
        "SMTP_HOST": "smtp.invalid",
        "SMTP_PORT": "587",
        "SMTP_USER": "mailer@example.com",
        "SMTP_FROM": "PlantDiseases <mailer@example.com>",
        "SMTP_PASSWORD_FILE": str(secret),
    }


def test_complete_mail_settings_build_an_smtp_mailer(tmp_path: Path):
    assert isinstance(build_mailer(Settings.from_env(_mail_env(tmp_path))), SmtpMailer)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SMTP_HOST", ""),
        ("SMTP_PORT", ""),
        ("SMTP_USER", ""),
        ("SMTP_FROM", ""),
        ("SMTP_PASSWORD_FILE", ""),
        ("SMTP_PORT", "smtp"),
        ("SMTP_PORT", "70000"),
        ("SMTP_FROM", "no-address"),
        ("SMTP_PASSWORD_FILE", "relative/password"),
        ("SMTP_PASSWORD_FILE", "/nonexistent/smtp-password"),
    ],
)
def test_unusable_mail_settings_turn_email_off_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, name: str, value: str
):
    settings = Settings.from_env(_mail_env(tmp_path) | {name: value})

    assert build_mailer(settings) is None
    assert "Email sign-in is off" in caplog.text
    assert "synthetic-smtp-password" not in caplog.text


@pytest.mark.parametrize(("mode", "content"), [(0o640, "secret"), (0o400, "\n")])
def test_shared_or_empty_password_file_is_refused(tmp_path: Path, mode: int, content: str):
    env = _mail_env(tmp_path)
    secret = Path(env["SMTP_PASSWORD_FILE"])
    secret.chmod(0o600)
    secret.write_text(content, encoding="utf-8")
    secret.chmod(mode)

    assert build_mailer(Settings.from_env(env)) is None


@pytest.mark.parametrize("with_mail", [False, True])
def test_service_starts_and_gates_email_on_mail_settings(
    db_engine, tmp_path: Path, with_mail: bool
):
    """The real wiring: no injected account service."""

    env = _mail_env(tmp_path)
    if not with_mail:
        for name in [name for name in env if name.startswith("SMTP_")]:
            del env[name]
    database = harness.TestDatabase(sessionmaker(bind=db_engine, expire_on_commit=False))
    application = create_app(Settings.from_env(env), database=database)

    assert application.state.account_service.mail_available is with_mail
    with TestClient(application) as client:
        # An unknown address gets no mail, so nothing leaves the process.
        response = client.post(
            "/v1/auth/email/password-reset/request", json={"email": "nobody@example.com"}
        )
    assert response.status_code == (202 if with_mail else 503)


def test_without_a_mailer_every_email_route_answers_503(api):
    tokens = harness._signup(api)
    api.service.mail_sender = None
    installation = str(uuid.uuid4())
    account = {"email": "a@example.com", "password": PASSWORD, "installation_id": installation}
    calls = [
        ("/v1/auth/email/register", account, None),
        ("/v1/auth/email/login", account, None),
        (
            "/v1/auth/email/verify",
            {"pending_signup_token": "x", "code": "000000", "installation_id": installation},
            None,
        ),
        ("/v1/auth/email/password-reset/request", {"email": "a@example.com"}, None),
        (
            "/v1/auth/email/password-reset/confirm",
            {"email": "a@example.com", "code": "000000", "new_password": NEW_PASSWORD},
            None,
        ),
        (
            "/v1/auth/email/reauth",
            {"password": PASSWORD, "scope": "deletion"},
            tokens["access_token"],
        ),
        (
            "/v1/auth/reauth",
            {"provider": "yandex", "provider_access_token": "mock-yandex-new", "scope": "link_email"},
            tokens["access_token"],
        ),
        (
            "/v1/auth/email/add",
            {"email": "a@example.com", "password": PASSWORD, "reauth_token": "x"},
            tokens["access_token"],
        ),
        ("/v1/auth/email/add/confirm", {"code": "000000"}, tokens["access_token"]),
    ]
    for path, body, token in calls:
        assert _outcome(_post(api, path, body, token)) == (503, "email_auth_unavailable"), path

    # Yandex is unaffected.
    assert api.client.get("/v1/me", headers=api.headers(tokens["access_token"])).status_code == 200


# ── transport and texts ───────────────────────────────────────────────


class _FakeSmtp:
    calls: list[tuple] = []
    offers_starttls = True

    def __init__(self, host, port, timeout, context=None):
        self.calls.append((type(self).__name__, host, port, context))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def starttls(self, *, context):
        if not self.offers_starttls:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported")
        self.calls.append(("starttls", context))

    def login(self, user, password):
        self.calls.append(("login",))

    def send_message(self, message):
        self.calls.append(("send", message["To"], message["From"]))


class _FakeImplicitTlsSmtp(_FakeSmtp):
    pass


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch):
    _FakeSmtp.calls = []
    _FakeSmtp.offers_starttls = True
    monkeypatch.setattr(mail_module.smtplib, "SMTP", _FakeSmtp)
    monkeypatch.setattr(mail_module.smtplib, "SMTP_SSL", _FakeImplicitTlsSmtp)
    return _FakeSmtp


def _smtp(port: int) -> SmtpMailer:
    return SmtpMailer(
        host="smtp.invalid", port=port, user="u", password="p", sender="a@example.com"
    )


def _assert_verifying(context: ssl.SSLContext) -> None:
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_smtp_upgrades_to_verified_tls_before_signing_in(fake_smtp):
    _smtp(587).send(MailMessage(to="b@example.com", subject="s", body="b"))

    assert [call[0] for call in fake_smtp.calls] == ["_FakeSmtp", "starttls", "login", "send"]
    _assert_verifying(fake_smtp.calls[1][1])


def test_port_465_uses_tls_from_the_first_byte(fake_smtp):
    _smtp(465).send(MailMessage(to="b@example.com", subject="s", body="b"))

    assert [call[0] for call in fake_smtp.calls] == ["_FakeImplicitTlsSmtp", "login", "send"]
    _assert_verifying(fake_smtp.calls[0][3])


def test_a_server_without_starttls_never_receives_the_password(fake_smtp):
    fake_smtp.offers_starttls = False

    with pytest.raises(smtplib.SMTPNotSupportedError):
        _smtp(25).send(MailMessage(to="b@example.com", subject="s", body="b"))
    assert not [call for call in fake_smtp.calls if call[0] in {"login", "send"}]


def test_mail_queue_delivers_and_logs_only_the_failure_type(caplog: pytest.LogCaptureFixture):
    delivered = MockMailer()
    queue = MailQueue(delivered)
    queue.send(MailMessage(to="b@example.com", subject="s", body="b"))
    queue.close()
    assert [message.to for message in delivered.outbox] == ["b@example.com"]

    class FailingMailer:
        def send(self, message):
            raise smtplib.SMTPRecipientsRefused({message.to: (550, b"no such user")})

    queue = MailQueue(FailingMailer())
    queue.send(MailMessage(to="private@example.com", subject="s", body="code 123456"))
    queue.close()
    assert "SMTPRecipientsRefused" in caplog.text
    assert "private@example.com" not in caplog.text and "123456" not in caplog.text


def test_mail_texts_are_bilingual_and_use_plain_punctuation():
    messages = [
        code_message(purpose=purpose, to="a@example.com", code="012345", minutes=15)
        for purpose in ("signup", "reset", "add")
    ]
    messages.append(already_registered_message(to="a@example.com"))
    for message in messages:
        text = message.subject + message.body
        assert re.search("[а-яё]", text) and re.search("[a-z]{4}", text)
        assert not set(text) & {"—", "–", "·"}
        assert "a@example.com" not in repr(message) and "012345" not in repr(message)
    assert all(message.body.count("012345") == 2 for message in messages[:3])


def test_encrypted_address_is_bound_to_its_fingerprint():
    protector = PushTokenProtector(b"k" * 32)
    first = protector.encrypt_email("a@example.com")
    assert first != protector.encrypt_email("a@example.com")
    assert b"example" not in first
    own = protector.email_fingerprint("a@example.com")
    assert protector.decrypt_email(first, fingerprint=own) == "a@example.com"
    with pytest.raises(InvalidTag):
        protector.decrypt_email(first, fingerprint=protector.email_fingerprint("b@example.com"))


# ── sign-up codes ─────────────────────────────────────────────────────


def test_no_account_exists_until_the_code_is_entered(api):
    registration, installation = _register(api, "new@example.com")
    token = registration.json()["pending_signup_token"]
    assert registration.json()["status"] == "verification_required"
    assert registration.json()["expires_in"] == 900

    assert _outcome(_complete(api, token, installation)) == (409, "email_verification_required")
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        record = transaction.scalar(select(EmailCode))
        code = harness._mailed_code(api, "new@example.com")
        assert code.encode() not in record.code_mac

    assert _verify(api, token, installation, code).status_code == 200
    # A retry after a lost response succeeds without spending anything.
    assert _verify(api, token, installation, code).status_code == 200
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(EmailCode)).consumed_at is not None
    assert _complete(api, token, installation).status_code == 201


def test_five_wrong_guesses_kill_the_code(api):
    registration, installation = _register(api, "guess@example.com")
    token = registration.json()["pending_signup_token"]
    code = harness._mailed_code(api, "guess@example.com")

    for _ in range(5):
        assert _outcome(_verify(api, token, installation, _wrong(code))) == (400, "invalid_code")
    assert _outcome(_verify(api, token, installation, code)) == (400, "invalid_code")
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(EmailCode)).attempts == 5


def test_a_signup_code_expires_after_15_minutes(api):
    registration, installation = _register(api, "late@example.com")
    token = registration.json()["pending_signup_token"]
    code = harness._mailed_code(api, "late@example.com")

    api.clock.advance(timedelta(minutes=15))

    assert _outcome(_verify(api, token, installation, code)) == (401, "pending_signup_expired")


def test_a_new_signup_retires_the_previous_code(api):
    first, first_installation = _register(api, "twice@example.com")
    old_code = harness._mailed_code(api, "twice@example.com")
    second, second_installation = _register(api, "twice@example.com")

    old = _verify(api, first.json()["pending_signup_token"], first_installation, old_code)
    assert _outcome(old) == (409, "pending_signup_replayed")
    new_token = second.json()["pending_signup_token"]
    new_code = harness._mailed_code(api, "twice@example.com")
    assert _verify(api, new_token, second_installation, new_code).status_code == 200


def test_a_registered_address_gets_a_signup_nothing_can_verify(api):
    harness._email_signup(api, email="taken@example.com")
    registration, installation = _register(api, "taken@example.com")
    token = registration.json()["pending_signup_token"]
    notice = api.mailer.outbox[-1]

    assert notice.to == "taken@example.com" and re.search(r"[0-9]{6}", notice.body) is None
    for guess in ("000000", "123456"):
        assert _outcome(_verify(api, token, installation, guess)) == (400, "invalid_code")
    assert _outcome(_complete(api, token, installation)) == (409, "email_verification_required")


def test_cancelling_a_signup_erases_the_staged_address(api):
    registration, installation = _register(api, "cancel@example.com")
    response = _post(
        api,
        "/v1/auth/signup/cancel",
        {
            "pending_signup_token": registration.json()["pending_signup_token"],
            "completion_id": str(uuid.uuid4()),
            "installation_id": str(installation),
        },
    )
    assert response.status_code == 204
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(PendingPasswordCredential)) is None


# ── password reset ────────────────────────────────────────────────────


def test_reset_answers_the_same_whether_or_not_the_address_exists(api):
    harness._email_signup(api, email="known@example.com")
    sent_before = len(api.mailer.outbox)

    known = _reset_request(api, "known@example.com")
    unknown = _reset_request(api, "unknown@example.com")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {
        "status": "code_sent_if_registered",
        "expires_in": 900,
    }
    assert [message.to for message in api.mailer.outbox[sent_before:]] == ["known@example.com"]
    code = harness._mailed_code(api, "known@example.com")
    assert _outcome(_reset_confirm(api, "known@example.com", _wrong(code))) == _outcome(
        _reset_confirm(api, "unknown@example.com", code)
    ) == (400, "invalid_code")
    for email in ("known@example.com", "unknown@example.com"):
        short = _reset_confirm(api, email, code, password="short")
        assert _outcome(short) == (400, "invalid_password")


def test_reset_changes_the_password_signs_devices_out_and_works_once(api):
    tokens = harness._email_signup(api, email="reset@example.com", password=PASSWORD)
    assert _reset_request(api, "reset@example.com").status_code == 202
    code = harness._mailed_code(api, "reset@example.com")

    assert _reset_confirm(api, "RESET@example.com", code).status_code == 204

    assert api.client.get("/v1/me", headers=api.headers(tokens["access_token"])).status_code == 401
    assert _outcome(_login(api, "reset@example.com", PASSWORD)) == (401, "invalid_credentials")
    assert _login(api, "reset@example.com", NEW_PASSWORD).json()["status"] == "authenticated"
    assert _outcome(_reset_confirm(api, "reset@example.com", code)) == (400, "invalid_code")


def test_reset_codes_expire_and_only_the_newest_works(api):
    harness._email_signup(api, email="expiry@example.com")
    _reset_request(api, "expiry@example.com")
    first = harness._mailed_code(api, "expiry@example.com")
    _reset_request(api, "expiry@example.com")
    second = harness._mailed_code(api, "expiry@example.com")
    if first != second:
        assert _outcome(_reset_confirm(api, "expiry@example.com", first)) == (400, "invalid_code")

    api.clock.advance(timedelta(minutes=15))

    assert _outcome(_reset_confirm(api, "expiry@example.com", second)) == (400, "invalid_code")


def test_an_unverified_stored_credential_signs_in_only_after_a_reset(api):
    """Rows from before revision 0012 have no address and no verification."""

    harness._email_signup(api, email="legacy@example.com", password=PASSWORD)
    with api.service.repository.transaction() as transaction:
        credential = transaction.scalar(select(PasswordCredential))
        credential.email_ciphertext = None
        credential.email_verified_at = None
    assert _outcome(_login(api, "legacy@example.com", PASSWORD)) == (401, "invalid_credentials")

    _reset_request(api, "legacy@example.com")
    code = harness._mailed_code(api, "legacy@example.com")
    assert _reset_confirm(api, "legacy@example.com", code).status_code == 204

    assert _login(api, "legacy@example.com", NEW_PASSWORD).status_code == 200
    with api.service.repository.transaction() as transaction:
        credential = transaction.scalar(select(PasswordCredential))
        assert credential.email_ciphertext and credential.email_verified_at


# ── rate limits ───────────────────────────────────────────────────────


def test_sending_is_limited_per_address_alike_for_known_and_unknown(api):
    harness._email_signup(api, email="busy@example.com")
    api.service.email_limits = EmailAbuseLimits()

    for email in ("busy@example.com", "ghost@example.com"):
        outcomes = [_outcome(_reset_request(api, email)) for _ in range(4)]
        assert outcomes == [(202, None)] * 3 + [(429, "rate_limited")], email


def test_refused_passwords_do_not_use_up_an_address_limit(api):
    body = {"email": "typo@example.com", "installation_id": str(uuid.uuid4())}
    for _ in range(12):
        short = _post(api, "/v1/auth/email/register", body | {"password": "too-short"})
        assert _outcome(short) == (400, "invalid_registration")
        reset = _reset_confirm(api, "typo@example.com", "000000", password="too-short")
        assert _outcome(reset) == (400, "invalid_password")

    assert _register(api, "typo@example.com")[0].status_code == 200
    assert _outcome(_reset_confirm(api, "typo@example.com", "000000")) == (400, "invalid_code")


def test_sending_is_limited_per_address_per_day():
    now = [0.0]
    limits = EmailAbuseLimits(monotonic=lambda: now[0])
    for number in range(10):
        limits.check_send(address_key="a", client_address=f"ip-{number}")
        now[0] += 16 * 60
    with pytest.raises(Exception, match="rate_limited"):
        limits.check_send(address_key="a", client_address="ip-last")


def test_sending_is_limited_per_ip_across_addresses(api):
    outcomes = [
        _outcome(_reset_request(api, f"member{number}@example.com")) for number in range(21)
    ]
    assert outcomes == [(202, None)] * 20 + [(429, "rate_limited")]


def test_guessing_is_limited_per_address_and_per_ip(api):
    outcomes = [
        _outcome(_reset_confirm(api, "target@example.com", "000000")) for _ in range(11)
    ]
    assert outcomes == [(400, "invalid_code")] * 10 + [(429, "rate_limited")]

    # The refused eleventh call already counted against the IP: 19 are left.
    spread = [
        _outcome(_reset_confirm(api, f"spread{number}@example.com", "000000"))
        for number in range(20)
    ]
    assert spread == [(400, "invalid_code")] * 19 + [(429, "rate_limited")]


# ── two sign-in methods on one account ────────────────────────────────


def _link_email_token(api, access_token: str) -> str:
    reauth = _post(
        api,
        "/v1/auth/reauth",
        {
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "link_email",
        },
        access_token,
    )
    assert reauth.status_code == 200, reauth.text
    return reauth.json()["reauth_token"]


def _add_email(api, access_token: str, email: str, reauth_token: str):
    return _post(
        api,
        "/v1/auth/email/add",
        {"email": email, "password": PASSWORD, "reauth_token": reauth_token},
        access_token,
    )


def test_a_yandex_member_adds_email_and_password(api):
    tokens = harness._signup(api)
    access = tokens["access_token"]
    reauth_token = _link_email_token(api, access)

    requested = _add_email(api, access, "Second@Example.com", reauth_token)
    assert requested.status_code == 202
    assert requested.json()["status"] == "verification_required"
    assert _outcome(_login(api, "second@example.com", PASSWORD)) == (401, "invalid_credentials")
    # The reauthentication was spent.
    reused = _add_email(api, access, "third@example.com", reauth_token)
    assert _outcome(reused) == (401, "reauth_expired")

    # Another session of the same account cannot finish it.
    other = harness._exchange(api, provider="yandex", provider_token="mock-yandex-new").json()
    code = harness._mailed_code(api, "second@example.com")
    foreign = _post(api, "/v1/auth/email/add/confirm", {"code": code}, other["access_token"])
    assert _outcome(foreign) == (400, "invalid_code")

    confirmed = _post(api, "/v1/auth/email/add/confirm", {"code": code}, access)
    assert confirmed.json() == {"status": "linked", "provider": "email"}
    profile = api.client.get("/v1/me", headers=api.headers(access)).json()
    assert [item["provider"] for item in profile["linked_providers"]] == ["email", "yandex"]
    assert profile["primary_provider"]["provider"] == "yandex"
    login = _login(api, "second@example.com", PASSWORD).json()
    assert login["profile"] == tokens["profile"]

    again = _post(
        api,
        "/v1/auth/reauth",
        {"provider": "yandex", "provider_access_token": "mock-yandex-new", "scope": "link_email"},
        access,
    )
    assert _outcome(again) == (409, "identity_already_linked")


def test_adding_another_members_address_looks_the_same_and_links_nothing(api):
    harness._email_signup(api, email="owner@example.com", nickname="OwnerLeaf")
    access = harness._signup(api)["access_token"]

    requested = _add_email(api, access, "owner@example.com", _link_email_token(api, access))

    assert requested.status_code == 202
    assert requested.json() == {
        "status": "verification_required",
        "provider": "email",
        "expires_in": 900,
    }
    assert re.search(r"[0-9]{6}", api.mailer.outbox[-1].body) is None
    refused = _post(api, "/v1/auth/email/add/confirm", {"code": "000000"}, access)
    assert _outcome(refused) == (400, "invalid_code")
    with api.service.repository.transaction() as transaction:
        assert len(list(transaction.scalars(select(PasswordCredential)))) == 1


def test_an_email_member_links_yandex(api):
    tokens = harness._email_signup(api, email="first@example.com", password=PASSWORD)
    access = tokens["access_token"]
    reauth = _post(
        api,
        "/v1/auth/email/reauth",
        {"password": PASSWORD, "scope": "link_yandex"},
        access,
    ).json()

    linked = _post(
        api,
        "/v1/auth/yandex/link",
        {"provider_access_token": "mock-yandex-new", "reauth_token": reauth["reauth_token"]},
        access,
    )

    assert linked.json() == {"status": "linked", "provider": "yandex"}
    exchanged = harness._exchange(api, provider="yandex", provider_token="mock-yandex-new").json()
    assert exchanged["status"] == "authenticated"
    assert exchanged["profile"] == tokens["profile"]


# ── deletion ──────────────────────────────────────────────────────────


def test_deletion_erases_the_address_before_community_cleanup_succeeds(api):
    tokens = harness._email_signup(api, email="bye@example.com", password=PASSWORD)
    access = tokens["access_token"]
    # A reset code and an unfinished sign-up also refer to the address.
    _reset_request(api, "bye@example.com")
    _register(api, "bye@example.com")
    api.service.deletion_dispatcher = lambda _actor_id: False
    reauth = _post(
        api, "/v1/auth/email/reauth", {"password": PASSWORD, "scope": "deletion"}, access
    ).json()

    deletion = api.client.request(
        "DELETE",
        "/v1/me",
        headers=api.headers(access),
        json={"reauth_token": reauth["reauth_token"]},
    )

    assert deletion.status_code == 202
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(PasswordCredential)).email_ciphertext is None
        assert transaction.scalar(select(PendingPasswordCredential)) is None
        assert transaction.scalar(select(EmailCode)) is None
        live = select(PendingSignup).where(
            PendingSignup.provider == "email",
            PendingSignup.consumed_at.is_(None),
            PendingSignup.cancelled_at.is_(None),
        )
        assert transaction.scalar(live) is None

    api.service.deletion_dispatcher = lambda _actor_id: True
    api.clock.advance(timedelta(days=1))
    assert api.service.process_deletion_jobs()["completed"] == 1
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(PasswordCredential)) is None
