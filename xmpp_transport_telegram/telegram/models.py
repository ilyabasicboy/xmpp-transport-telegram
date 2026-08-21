from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TelegramDialog:
    peer_id: int
    title: str
    username: Optional[str] = None
    phone: Optional[str] = None
    is_group: bool = False
    is_channel: bool = False
