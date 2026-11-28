"""Account mail in Russian and English, in one message.

The service does not know which language a reader prefers, so each message
carries both. Plain punctuation only: no em or en dashes and no middle dots.
"""

from __future__ import annotations

from app.mail import MailMessage

_FOOTER_RU = "Это письмо отправлено автоматически, отвечать на него не нужно."
_FOOTER_EN = "This message was sent automatically. Please do not reply."

_CODE_TEXTS = {
    "signup": (
        "PlantDiseases: код регистрации / sign-up code",
        "Ваш код для создания аккаунта PlantDiseases: {code}",
        "Your code to create a PlantDiseases account: {code}",
    ),
    "reset": (
        "PlantDiseases: сброс пароля / password reset",
        "Ваш код для сброса пароля PlantDiseases: {code}",
        "Your code to reset your PlantDiseases password: {code}",
    ),
    "add": (
        "PlantDiseases: подтверждение адреса / confirm your email",
        "Ваш код, чтобы добавить эту почту к аккаунту PlantDiseases: {code}",
        "Your code to add this email to your PlantDiseases account: {code}",
    ),
}


def code_message(*, purpose: str, to: str, code: str, minutes: int) -> MailMessage:
    subject, line_ru, line_en = _CODE_TEXTS[purpose]
    body = "\n".join(
        (
            line_ru.format(code=code),
            f"Код действует {minutes} минут и подходит только один раз.",
            "Никому его не сообщайте. Если вы не запрашивали код, просто удалите письмо.",
            _FOOTER_RU,
            "",
            line_en.format(code=code),
            f"The code works once and expires in {minutes} minutes.",
            "Do not share it. If you did not ask for it, you can delete this message.",
            _FOOTER_EN,
            "",
        )
    )
    return MailMessage(to=to, subject=subject, body=body)


def already_registered_message(*, to: str) -> MailMessage:
    """Sent instead of a code when the address already signs in to an account."""

    body = "\n".join(
        (
            "Кто-то указал эту почту, чтобы создать аккаунт PlantDiseases "
            "или добавить её к другому аккаунту.",
            "Эта почта уже используется для входа, поэтому ничего не изменилось.",
            "Если это были вы, войдите с этой почтой или сбросьте пароль в приложении.",
            "Если нет, просто удалите письмо.",
            _FOOTER_RU,
            "",
            "Someone entered this email to create a PlantDiseases account or to add it to another account.",
            "This email already signs in to an account, so nothing has changed.",
            "If this was you, sign in with this email or reset your password in the app.",
            "If not, you can delete this message.",
            _FOOTER_EN,
            "",
        )
    )
    return MailMessage(
        to=to,
        subject="PlantDiseases: эта почта уже зарегистрирована / already registered",
        body=body,
    )
