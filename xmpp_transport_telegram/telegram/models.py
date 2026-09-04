from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TelegramAvatar:
    photo_id: str
    content: bytes
    mime_type: str = "image/jpeg"
    variant: str = "small"


@dataclass(frozen=True)
class TelegramDialog:
    peer_id: int
    title: str
    username: Optional[str] = None
    phone: Optional[str] = None
    is_group: bool = False
    is_channel: bool = False
    avatar: Optional[TelegramAvatar] = None
    avatar_photo_id: Optional[str] = None
    avatar_download_failed: bool = False


@dataclass(frozen=True)
class TelegramContact:
    peer_id: int
    title: str
    username: Optional[str] = None
    phone: Optional[str] = None
    avatar: Optional[TelegramAvatar] = None
    avatar_photo_id: Optional[str] = None
    avatar_download_failed: bool = False


@dataclass(frozen=True)
class TelegramForwardReference:
    source_peer_id: int
    message_id: str


@dataclass(frozen=True)
class TelegramMedia:
    url: str
    name: str
    mime_type: str
    size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[int] = None
    voice: bool = False
