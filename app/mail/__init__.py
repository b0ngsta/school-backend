"""Outgoing email. The rest of the app only talks to `app.services.email`,
which picks the backend from here: an SMTP server (EMAIL_BACKEND=smtp, the
default when SMTP_HOST is set) or the log (EMAIL_BACKEND=console)."""
from __future__ import annotations

import threading

from app.core.config import settings
from app.mail.base import Mailer, OutgoingEmail

__all__ = ["Mailer", "OutgoingEmail", "get_mailer", "set_mailer"]

_mailer: Mailer | None = None
_lock = threading.Lock()


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        with _lock:
            if _mailer is None:
                _mailer = _build()
    return _mailer


def set_mailer(mailer: Mailer | None) -> None:
    """Swap the backend (tests, maintenance scripts)."""
    global _mailer
    _mailer = mailer


def _build() -> Mailer:
    if settings.email_backend == "smtp":
        from app.mail.smtp import SmtpMailer

        return SmtpMailer.from_settings(settings)
    from app.mail.console import ConsoleMailer

    return ConsoleMailer(show_body=not settings.is_production)
