from __future__ import annotations

from pydantic import BaseModel


class RawEmail(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    body: str
