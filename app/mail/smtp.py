"""Delivery through any SMTP server (Amazon SES, Zoho ZeptoMail, Brevo,
Postmark, Gmail/Google Workspace, …)."""
from __future__ import annotations

import os
import smtplib
import ssl
from collections.abc import Sequence
from email.utils import formataddr, parseaddr

from app.core.config import Settings
from app.core.env import default_ca_bundle
from app.mail.base import OutgoingEmail, to_mime


class SmtpMailer:
    background = True

    def __init__(self, *, host: str, port: int, username: str, password: str, security: str,
                 timeout: int, sender: str):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.security, self.timeout = security, timeout
        self.sender = sender

    @classmethod
    def from_settings(cls, settings: Settings) -> SmtpMailer:
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
            timeout=settings.smtp_timeout_seconds,
            sender=sender_address(settings),
        )

    def send(self, messages: Sequence[OutgoingEmail]) -> None:
        if not messages:
            return
        with self._connect() as smtp:  # one connection for the whole batch
            for message in messages:
                smtp.send_message(to_mime(message, self.sender))

    def _connect(self) -> smtplib.SMTP:
        bundle = default_ca_bundle()
        context = ssl.create_default_context(cafile=bundle if os.path.exists(bundle) else None)
        if self.security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        try:
            if self.security == "starttls":
                smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password)
        except BaseException:
            smtp.close()
            raise
        return smtp


def sender_address(settings: Settings) -> str:
    """EMAIL_FROM as given ("School <no-reply@school.in>"), or the SMTP user
    with the school's name as display name."""
    raw = settings.email_from or settings.smtp_username or "no-reply@localhost"
    name, address = parseaddr(raw)
    return raw if name else formataddr((settings.school_name, address or raw))
