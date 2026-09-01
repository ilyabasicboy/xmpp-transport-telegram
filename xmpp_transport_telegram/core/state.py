from dataclasses import dataclass
from enum import Enum


class AuthStatus(str, Enum):
    WAITING_QR = "waiting_qr"
    WAITING_CODE = "waiting_code"
    WAITING_PASSWORD = "waiting_password"
    CONNECTED = "connected"
    FAILED = "failed"


@dataclass(frozen=True)
class DirectReplyContext:
    message_id: str
    body: str
    sender: str
    recipient: str
    fake_outgoing: bool = False
