"""Keeps sent messages in a list (tests)."""
from __future__ import annotations

from collections.abc import Sequence

from app.mail.base import OutgoingEmail


class MemoryMailer:
    background = False

    def __init__(self):
        self.outbox: list[OutgoingEmail] = []
        self.fail_next = 0  # make the next N send() calls raise (retry tests)

    def send(self, messages: Sequence[OutgoingEmail]) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("simulated mail server failure")
        self.outbox.extend(messages)

    def to(self, address: str) -> list[OutgoingEmail]:
        return [m for m in self.outbox if m.to == address]
