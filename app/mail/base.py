"""What every mail backend accepts, and how a message is turned into MIME."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Protocol


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str | None = None


class Mailer(Protocol):
    # True: deliver on a background thread (slow network backends, e.g. SMTP)
    background: bool

    def send(self, messages: Sequence[OutgoingEmail]) -> None:
        """Deliver the messages; raise if delivery failed."""


def to_mime(message: OutgoingEmail, sender: str) -> EmailMessage:
    """Build the MIME message. EmailMessage rejects CR/LF in headers, so a
    crafted address or subject can never inject extra headers."""
    mime = EmailMessage()
    mime["From"] = sender
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(usegmt=True)
    domain = parseaddr(sender)[1].rpartition("@")[2] or "localhost"
    mime["Message-ID"] = make_msgid(domain=domain)
    mime.set_content(message.text)
    if message.html:
        mime.add_alternative(message.html, subtype="html")
    return mime
