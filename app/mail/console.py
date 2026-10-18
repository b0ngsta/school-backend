"""No SMTP server configured: write emails to the log instead.

In development the whole message is logged, so reset codes can be read from
the console. In production only the recipient and subject are logged (never
a code), together with a warning that nothing was sent.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from app.mail.base import OutgoingEmail

log = logging.getLogger("app.mail")


class ConsoleMailer:
    background = False

    def __init__(self, *, show_body: bool):
        self.show_body = show_body

    def send(self, messages: Sequence[OutgoingEmail]) -> None:
        for m in messages:
            if self.show_body:
                log.info("EMAIL (not sent, development) to=%s subject=%r\n%s", m.to, m.subject, m.text)
            else:
                log.warning("EMAIL NOT SENT (no SMTP_HOST configured) to=%s subject=%r", m.to, m.subject)
