"""Outgoing email. Development prints to the log, tests keep an outbox, production uses SMTP."""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("ask.email")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


@dataclass
class MemoryEmailSender:
    """Keeps messages in memory. Used by tests."""

    outbox: list[OutgoingEmail] = field(default_factory=list)

    def send(self, message: OutgoingEmail) -> None:
        self.outbox.append(message)


class ConsoleEmailSender:
    """Development only: logs the whole message, including one-time links, so a developer can click them."""

    def send(self, message: OutgoingEmail) -> None:
        logger.warning(
            "DEV EMAIL (console backend)\nTo: %s\nSubject: %s\n\n%s",
            message.to,
            message.subject,
            message.body,
        )


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, message: OutgoingEmail) -> None:
        settings = self._settings
        mail = EmailMessage()
        mail["From"] = settings.email_from
        mail["To"] = message.to
        mail["Subject"] = message.subject
        mail.set_content(message.body)
        assert settings.smtp_host is not None  # noqa: S101  (validated at startup)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            # An explicit context: with none, Python encrypts but does not check who it is talking to, so
            # anyone
            # on the path could read the SMTP password and every sign-in link.
            smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(mail)


def build_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":
        return SmtpEmailSender(settings)
    if settings.email_backend == "memory":
        return MemoryEmailSender()
    return ConsoleEmailSender()


def invitation_email(to: str, company: str, link: str, hours: int) -> OutgoingEmail:
    return OutgoingEmail(
        to=to,
        subject=f"You are invited to join {company} on ASK",
        body=(
            f"You have been invited to join {company} on ASK.\n\n"
            f"Create your account: {link}\n\n"
            f"This link works once and expires in {hours} hours. "
            "If you were not expecting it, ignore this email."
        ),
    )


def password_reset_email(to: str, link: str, minutes: int) -> OutgoingEmail:
    return OutgoingEmail(
        to=to,
        subject="Reset your ASK password",
        body=(
            f"Someone asked to reset the password for this ASK account.\n\nChoose a new password: {link}\n\n"
            f"This link works once and expires in {minutes} minutes. If it was not you, ignore this email."
        ),
    )


def verify_email_email(to: str, link: str, hours: int) -> OutgoingEmail:
    return OutgoingEmail(
        to=to,
        subject="Confirm your email for ASK",
        body=f"Confirm this email address for your ASK account: {link}\n\nThe link expires in {hours} hours.",
    )
